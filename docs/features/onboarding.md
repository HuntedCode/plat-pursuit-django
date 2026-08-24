# New-User Onboarding

Two surfaces teach the site's novel systems (Career/Jobs/Contracts, badge medallions) to fresh
eyes: the first-sync waiting room and a one-shot explainer card on Career. Both shipped 2026-08
after the rebuild settled; a prior three-tour coach-mark system was deliberately deleted in the
chrome rebuild, and this is its rebuilt-from-scratch replacement in a different shape (no
spotlight tours: the sync wait is a captive window, and in-context cards teach at the moment
of curiosity).

## Surface 1: the syncing hero on the gates surface (`templates/home/_hero_syncing.html`)

Since the gates merge, the syncing state renders on `home/landing.html` with a live status hero
above the landing's real sections. A first sync takes 10-30 minutes and the user has nothing to
do but wait. The page uses that window three ways (hero wiring in `static/js/syncing.js` +
`static/css/components/syncing.css`; the progress/elapsed mirrors are inline in the landing's
syncing branch):

1. **Personalized greeting.** PSN's own trophy totals (`Profile.earned_trophy_summary`,
   `trophy_level`) land seconds into the sync via `update_profile_from_legacy`, long before the
   per-game walk finishes. `HomeView` passes `psn_found` (or `None` before the summary lands)
   and the greeting says "We found 8,412 trophies and 71 platinums on your PSN profile". Both
   sentence variants render server-side (`data-psn-line` / `data-psn-pending`); the poll
   payload's additive `psn_found` key upgrades a too-early page live.
2. **The tour is the landing itself.** The gates merge retired the bespoke walkthrough
   carousel: the landing's real sections (medallion shelf with the 3D inspect modal, the live
   ratings carousel, the showcase Profile Card, the heartbeat band) render below the hero.
   The site's best showcase is the tour, at full fidelity, with zero duplicated upkeep.
3. **The banked-so-far tally.** The per-type denorms (total_plats/golds/silvers/bronzes)
   climb LIVE during the walk via the EarnedTrophy post_save signal (only total_trophies
   waits for finalize), so the payload's existing `stats` block already carries the growing
   tally at zero extra cost. The hero sums the four figures and ticks the total old-to-new
   with the house count-up alongside the progress bar; being stats-derived also keeps it
   definitionally consistent with the finale's numbers.
4. **The enter moment.** On a FIRST sync (`is_initial_sync`), the `synced` transition swaps
   the status card in place: "Your Pursuer has emerged" (the phrase is from
   `docs/design/gamification-plan.md`'s design intent for this exact moment), the final
   trophy counts, and one "Enter your Pursuit" CTA. No auto-reload; the user arrives on the
   Home on purpose. Quick refreshes and error transitions keep the old reload behavior.

See [home-page.md](home-page.md) for the router, the event contract, and the finalize phases.

## Surface 2: the Career "how it works" modal

`templates/trophies/partials/career/_career_explainer.html`: a first-visit MODAL in the badge
howto's mold (auto-opens once, focus trap, Escape/scrim/Got-it dismiss), with three beats in
Jeffrey's own words: 25 jobs / five disciplines, Contracts (curated game sets assigned jobs by
genre and theme), and Pursuer (complete contracts to earn XP toward jobs and the overall
level). Jeffrey chose the modal over the earlier inline card ("more in the face" for
information that matters).

- **Server-side auto-open gate**: the modal markup renders for everyone, but `data-auto` (the
  auto-open arm) renders only while `career_explainer` is absent from `request.user.ui_flags`.
- **Reachable forever, unlike badges**: the badge howto is one-shot because its teaching has a
  fuller home at /badges/how-it-works (a recall button would let two copies drift). Career has
  no long-form page -- the modal IS the teaching -- so the summary card's `.pp-edhint`
  ("How does my Career work?", `data-career-open`) reopens it. One copy, no drift. Reopens
  close silently: only an armed (first-visit) dismissal writes the flag.
- **The collage**: below the beats, the three ideas as objects with chevrons between them --
  a quad of real job icons, the badge howto's own cover fan (a contract IS a set of games),
  and a Pursuer ring. Arm-then-light choreography replays on every open (fan snaps open,
  arc sweeps); classless markup is the FINAL state, so no-JS and reduced-motion always see
  the finished collage.
- **The choreography gate**: Career's own on-load motion (count-ups, rank count-down,
  dossier reveals) would play out behind the auto-opened modal's scrim on the one visit it
  exists for. The partial publishes `ppAfterCareerHowto` synchronously; the page's three
  kick-offs ride it and fire on close (or immediately when no auto-open will happen).
- **ONE dev panel per page**: `.ccx-dev` is fixed to left/bottom 12px, so a second instance
  stacks over the first and eats its clicks (shipped once, guard-tested now). New dev
  affordances join the page's existing panel as extra rows.
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

- **`/?preview=syncing`**: staff/moderators see the syncing state from a synced account
  (mirrors `/?preview=landing`). The preview forces the first-sync view, so the greeting,
  progress bar, and finale all render.
- **DEBUG simulate panel** on the syncing page (`data-sync-dev`, `.ccx-dev` styling):
  "Simulate progress" steps canned poll payloads; "Simulate live sync" runs 20 eased ticks
  over ~24 seconds with the bar and tally climbing together (re-click restarts, never
  auto-finishes); "Simulate synced" dispatches the
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
