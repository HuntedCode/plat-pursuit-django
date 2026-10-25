# Celebration Moments

A hunter's wins, celebrated once, where they chose to look. A `Moment` is recorded **when the thing happens**
(a badge awarded, a platinum earned) and **played once** in the browser: badge moments on the hunter's next
My Pursuit page, platinums behind a button as a reel. Badge rewards phase 3 (owner, 2026-10-25). Full Set and
Mastery moments join in phase 4.

## Architecture Overview

**Why recorded at the earn.** The platinum confetti this replaces fired from a notifications-inbox poll on
whatever page the hunter opened next, so it went off nowhere near what it celebrated. It was removed in
`adbffdd3` ("That moment belongs to the rebuild, fired from the earn"). Moments are written where the earn
happens and read only on My Pursuit pages.

**The kinds.**

| Kind | Written by | When |
|---|---|---|
| `welcome` | `moment_service.start_moments` (end of `_job_sync_complete`) | A hunter's FIRST finished sync, once. Counts their badges and platinums. Also stamps `Profile.moments_since` |
| `badge` | `badge_apply.apply_changes` (every award: sync, nightly, rechecks) | A badge is awarded. A re-earn records a new one (owner: it plays every time it is earned) |
| `platinum` | `psn_api_service.create_or_update_earned_trophy_from_trophy_data` | Sync sees a platinum newly earned within `PLATINUM_RECENCY` (2 days). Shovelware included: it is the hunter's own |
| `milestone` | `moment_service.record_milestones` (after `recompute_on_sync`) | A **platinum-count** milestone tier crossed in a sync: the reel's last beat |

**First sync = one summary, not a flood.** Nothing is recorded for a hunter until `Profile.moments_since` is
set, which happens at the end of their first finished sync, together with the one `welcome` moment
(owner, 2026-10-25). Everything a first sync awards is therefore summarised, never queued one by one.

**Playing.**

| Queue | Kinds | How it plays |
|---|---|---|
| auto | welcome, badge | Auto-plays on a My Pursuit page, ONE AT A TIME ("1 of 3", Next, Skip all), after any other modal on the page has had its turn |
| reel | platinum, milestone | **Never auto-plays.** A pill at the end of the My Pursuit strip ("3 new platinums") opens it |

Settings > Library > **Play celebrations** (`CustomUser.play_celebrations`, default on) stops auto-play. Off,
the strip's pill counts and opens every waiting moment.

**Played once.** A moment is marked played the moment it is ON SCREEN (`POST /api/v1/moments/played/`), so
closing mid-queue never replays what was seen, on this device or another. Skip all marks the rest of the open
queue played. Unplayed moments older than `MOMENT_TTL` (14 days) are ignored: the badge itself is still on
the wall.

## File Map

| File | Purpose |
|---|---|
| `trophies/models.py` (`Moment`, `Profile.moments_since`) | The row and the "moments started" stamp |
| `users/models.py` (`CustomUser.play_celebrations`) | The off switch |
| `trophies/services/moment_service.py` | Recording, the pending read (`pending`, `for_request`), `mark_played`, what each moment shows (`build_view`) |
| `trophies/templatetags/moments.py` | `{% moments_overlay %}` (base.html) and `{% moments_pill %}` (the strip) |
| `templates/partials/moments/_overlay.html`, `_pill.html` | The player (a `.pp-detail-modal`) with one `<template>` per moment, and the pill |
| `static/js/moments.js` | The player: queues, auto-play wait, played POSTs, pill refresh |
| `static/css/components/moments.css` | Player, moment layouts, pill |
| `api/moment_views.py` | The played endpoint |
| `trophies/migrations/0352_backfill_moments_since.py` | Starts moments for everyone already here at deploy |

## Data Model

`Moment`: `profile`, `kind`, `group_badge` (badge), `earned_trophy` (platinum), `data` (welcome counts, the
platinum's number, a milestone's name + threshold), `occurred_at` (orders the queue), `created_at` (the
expiry clock), `played_at`. FKs reach Profile, GroupBadge and EarnedTrophy only, so `Concept.absorb()` needs
no branch.

| Constraint / index | Why |
|---|---|
| `moment_pending_idx` (profile, created_at) WHERE unplayed | The per-page read is a seek on a small index, never the hunter's history |
| `moment_one_pending_badge` (profile, group_badge) WHERE badge AND unplayed | A badge that flips off and on waits as ONE moment |
| `moment_once_per_platinum` (earned_trophy) WHERE platinum | A platinum is celebrated once, ever |

## Key Flows

### A page with something waiting

1. `base.html` and the strip call the tags only when `hub_section == 'my_pursuit'`.
2. `for_request` reads the pending moments once per request (memoised on the request). Pages outside My
   Pursuit read nothing.
3. With anything waiting, `build_view` builds the cards in bounded reads (one hold read for all badges, one
   read each for the platinums' trophies, games and start dates, plus a share link per platinum).
4. `moments.js` marks waiting badges as already minted for the Collection (only when it will auto-play), waits
   1.1s, then waits for a clear stage (no other `.pp-detail-modal` open, no claim ceremony) and plays the auto
   queue.

## API Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/moments/played/` | Session + CSRF | `{"ids": [...]}` (1..30 positive ints): mark the hunter's own moments played. Returns `{"played": n}` |

## Integration Points

- [Badge System](../architecture/badge-system.md): `apply_changes` records a badge moment on every award.
- [Token Keeper](../architecture/token-keeper.md): the finishing phase records milestone moments, then starts a
  first-syncing hunter's moments.
- [Milestones](../design/milestones-revamp.md): its unbuilt "congrats you hit this" moment is this reel's last beat.
- [Onboarding](onboarding.md): the welcome moment is the first thing a new hunter sees on a My Pursuit page.
- [Notification System](../architecture/notification-system.md): the parked inbox's platinum confetti is replaced by this.

## The flourish, and how to see it

One beat per moment, in the earn vocabulary (visual-identity.md), never confetti. The CSS carries the entrances
(`moments.css`), and `moments.js` lands the sparks (`PlatPursuit.sparkBurst`) on the beat the object arrives:

| Moment | Beat |
|---|---|
| welcome | the lines cascade in, the tallies count up |
| badge | the medallion is struck (drops in small, settles), its bloom flares, sparks fly and its own mint pops |
| platinum | the cover is dealt in (a turn and a settle), a platinum glow flares, sparks fly, a light sweeps across it, the number counts up |
| milestone | the number swells in, a platinum ring pulses out, the biggest burst of sparks |

**Sunlight** (owner, 2026-10-25): once the object has landed, slow rays rise behind it and turn (one turn in 48s)
for as long as the moment is up. A badge's rays wear its TIER (the medallion's metals, shared as `--med-tier-*`
tokens in `badge-medallion.css`), iridescent when the badge is holographic; platinum and milestone rays are
platinum. Phase 4's mastered badge turns them gold with its rim and crown. The rays are a mask (repeating wedges
intersected with a radial fade) over a colour fill, so the fill alone decides the colour, gradient included.
They outgrow the dialog, so the stage clips them sideways (`overflow-x: clip`, which makes no scroll box).

Reduced motion gets the end state: no sparks, no movement, the rays still. Every waiting moment's art is preloaded at page load,
because a `<template>`'s images only start loading when it is cloned in.

**`?preview=moments`** on any My Pursuit page (staff or moderator, `core/previews.py`) plays a sample queue
built from the viewer's own data: the welcome, their latest badge, their two latest platinums and a platinum
milestone. The moments are unsaved with negative ids, nothing is posted and the Collection's minted set is
untouched, so it replays on every reload, on dev or on the live site.

## Gotchas and Pitfalls

- **Recording never raises.** `record_badge` and `record_platinum` run inside `apply_changes`' transaction and a
  sync's trophy batch. Each has its own savepoint and swallows database errors (logged): a failed celebration
  must never roll back a badge award or a sync.
- **An expired waiting badge is REFRESHED, not left.** It would otherwise hold the one-pending slot forever and
  swallow every later earn of that badge.
- **The platinum number is stored at record time** from the live `total_plats` denorm (the EarnedTrophy signal
  bumps it before the hook runs). Counting a hunter's platinums by date at play time would scan a whale's
  whole trophy history.
- **A revoked badge's moment does not play.** `pending` filters badge moments to badges still held.
- **The Collection's mint and this ceremony are one celebration.** `moments.js` (deferred, so it runs before
  collection.js's DOMContentLoaded `initMint`) adds waiting badges to the Collection's per-device minted set.
- **The phone row of the strip is `lg:hidden`.** Any `display` rule on `.pp-sub__mrow` outside a below-`lg`
  media query outranks that utility and puts a second strip on desktop.
- **Not a `ui_flags` shape.** Played state is a server column per moment, not a per-user flag
  (see [Onboarding](onboarding.md) for the `ui_flags` shapes).
- **Deploy backfill.** 0352 stamps every profile with an account except one whose FIRST sync is running
  (`syncing` with no badge yet), so veterans never get the newcomer's welcome.
