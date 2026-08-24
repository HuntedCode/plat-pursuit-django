# New-User Onboarding

Two surfaces teach the site's novel systems (Career/Jobs/Contracts, badge medallions) to fresh
eyes: the first-sync waiting room and a one-shot explainer card on Career. Both shipped 2026-08
after the rebuild settled; a prior three-tour coach-mark system was deliberately deleted in the
chrome rebuild, and this is its rebuilt-from-scratch replacement in a different shape (no
spotlight tours: the sync wait is a captive window, and in-context cards teach at the moment
of curiosity).

## Surface 1: the sync-wait walkthrough (`templates/home/syncing.html`)

A first sync takes 10-30 minutes and the user has nothing to do but wait. The page uses that
window three ways (all wiring in `static/js/syncing.js` + `static/css/components/syncing.css`):

1. **Personalized greeting.** PSN's own trophy totals (`Profile.earned_trophy_summary`,
   `trophy_level`) land seconds into the sync via `update_profile_from_legacy`, long before the
   per-game walk finishes. `HomeView` passes `psn_found` (or `None` before the summary lands)
   and the greeting says "We found 8,412 trophies and 71 platinums on your PSN profile". Both
   sentence variants render server-side (`data-psn-line` / `data-psn-pending`); the poll
   payload's additive `psn_found` key upgrades a too-early page live.
2. **The walkthrough carousel.** Five static panels (Build a Career, Chase Badges, Your
   library organized, Months worth keeping, Boards to climb) with CSS slide art. Auto-advance
   7s, dots, touch swipe, in-view gate, reduced-motion aware. Deliberately no per-user data:
   mid-sync the visitor has none yet. The carousel pattern is copied from landing.js's ratings
   carousel; a third consumer is the signal to extract a shared primitive into utils.js.
3. **The enter moment.** On a FIRST sync (`is_initial_sync`), the `synced` transition swaps
   the status card in place: "Your Pursuer has emerged" (the phrase is from
   `docs/design/gamification-plan.md`'s design intent for this exact moment), the final
   trophy counts, and one "Enter your Pursuit" CTA. No auto-reload; the user arrives on the
   Home on purpose. Quick refreshes and error transitions keep the old reload behavior.

See [home-page.md](home-page.md) for the router, the event contract, and the finalize phases.

## Surface 2: the Career first-visit explainer

`templates/trophies/partials/career/_career_explainer.html`, included between the Pursuer hero
and the claimable rail. A compact education card with three beats: Pursuer Level, five
disciplines / 25 jobs, and Contracts (contract teaching folds in here; there is no separate
Contracts explainer). Dismiss button "Got it".

- **Server-side render gate**: `CareerView` passes `show_career_explainer` = the
  `career_explainer` key is absent from `request.user.ui_flags`. Returning users get a full
  non-render, never a flash.
- **Career only, by design**: a Collection explainer was explicitly rejected (the badge
  "how badges work" modal already owns that teaching moment).

## The `ui_flags` pattern

`CustomUser.ui_flags` (JSONField, mirrors `browse_defaults`) holds one-shot education flags:
presence of a key means dismissed. Writes go through the quick-settings API's `ui_flag` branch
(`api/user_settings_views.py`, whitelist `UI_FLAGS`), a read-modify-write that preserves other
keys. Adding a new explainer = add its key to `UI_FLAGS`, gate its render server-side, POST on
dismiss.

Client discipline (from the badge howto + timezone modal prior art):

- Fire-and-forget `PlatPursuit.API.post('/api/v1/user/quick-settings/', {setting: 'ui_flag',
  value: '<flag>'})` with **no success toast** (dismissing a hint should not earn a
  notification).
- On failure, fall back to localStorage (`pp-explainer-<surface>-seen`) so the card stays
  hidden on this device, and self-heal: on the next load, a set fallback key re-fires the POST
  and clears itself on success.

## Dev affordances

- **`/?preview=syncing`**: staff/moderators see the sync-wait page from a synced account
  (mirrors `/?preview=landing`). `is_initial_sync` renders False for a synced previewer.
- **DEBUG simulate panel** on the syncing page (`data-sync-dev`, `.ccx-dev` styling):
  "Simulate progress" steps canned poll payloads; "Simulate synced" dispatches the
  status-changed event BEFORE a trailing progress event, which is navsync's real ordering, so
  the idempotent stat re-fill is what gets exercised. Drives the whole client state machine
  without a real sync.
- **DEBUG replay panel** on Career: re-arms and replays the explainer's first-visit entrance.

## Gotchas and Pitfalls

- **Event ordering is load-bearing**: navsync dispatches `platpursuit:sync-status-changed`
  before the same poll's `platpursuit:sync-progress`. The finale's stat fill must stay
  idempotent and re-run on the trailing event, or it shows one-poll-stale counts forever.
- **Never poll from the syncing page**: navsync owns the network; consumers subscribe to its
  events. Killing `#nav-sync` on that page freezes everything.
- **`get_total_trophies_from_summary()` returns `None` on an empty summary**: `psn_found` is
  `None` (not a zeroed dict) until the summary lands; templates must branch, never render raw.
- **The finale is first-syncs only**: `is_initial_sync` gates the block's render, and its
  absence is what routes syncing.js to the reload path for quick refreshes. Don't "simplify"
  them into one path; a returning user mid-refresh wants straight in.
- **Explainer copy rules**: earnest and additive, fun first, no em dashes, no forging/minting
  vocabulary, and the jobs count is 25 across five disciplines.
- **The comment-names-the-forbidden-string trap**: guard tests scan the explainer partial for
  banned classes; comments must describe them without naming them.
