# New-User Onboarding

Two surfaces teach the site's novel systems (Career/Jobs/Contracts, badge medallions) to fresh
eyes: the first-sync waiting room and a one-shot explainer card on Career. Both shipped 2026-08
after the rebuild settled; a prior three-tour coach-mark system was deliberately deleted in the
chrome rebuild, and this is its rebuilt-from-scratch replacement in a different shape (no
spotlight tours: the sync wait is a captive window, and in-context cards teach at the moment
of curiosity).

## Before the sync: sign up, confirm, link PSN

Rebuilt 2026-10 as one flow in one shell (`templates/account/base_auth.html`, see the Auth Shell
section of [design-system.md](../reference/design-system.md); record:
[onboarding-rebuild.md](../design/reviews/onboarding-rebuild.md)).

| Step | What the hunter sees | Notes |
|---|---|---|
| The landing's claim (optional start) | "Look up" finds a PSN name; a profile nobody has an account on adds "That's you? Sign up free to claim it." | The link is sign-up with `next=/accounts/link-psn/?psn=<name>`. `claimable` comes from the search and status payloads (no account on it). Sign-up also keeps the name in the session for that account only (`users/services/claimed_psn.py`). Record: [landing-pass.md](../design/reviews/landing-pass.md) |
| Sign up | One email, one password, "Next: confirm your email, then link your PSN." | `ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*']`. The honeypot and the browser timezone input stay |
| Check your inbox | The address the link went to, "Send another link" (the wait counts down), "Wrong address? Fix it" | `users/services/pending_signup.py`: one session record, written by `CustomAccountAdapter.send_confirmation_mail` after a real send |
| Confirm | The link signs them in (`ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION`) and lands on the no-PSN welcome, or on the sign-up's `next` | allauth keeps `next` on the pending login and resumes it on confirmation, in the same browser and within `ACCOUNT_LOGIN_TIMEOUT` (15 minutes, the default). Later, or in another browser, the link verifies the address and lands on sign-in (allauth's anonymous confirmation redirect); after signing in they reach the no-PSN welcome |
| Link PSN 1 to 3 | Name, then the code with where About is, then verifying | Step 1 fills in the landing's claim: `?psn=<name>`, else the session's claimed name (which outlives allauth's pending login and "Fix it", and is shown only to the account that claimed it), used only when the field itself accepts it, and forgotten once step 1 is submitted. Step 2 regenerates an expired code and re-checks ownership on every return (FV-20) |

**Resend and fix-it act only on this browser's sign-up.** Neither takes an address from the request;
both read the session record. Fix-it deletes the account only while it is unverified, has never signed in
and is not staff, then clears allauth's pending login for it and reopens sign up with the address
prefilled (allauth's `?email=`). A staff resend from the admin is never recorded (the sender is signed
in), so a staff member's own "Check your inbox" can never offer to delete someone else's account.

## Surface 1: the syncing hero on the gates surface (`templates/home/_hero_syncing.html`)

Since the gates merge, the syncing state renders on `home/landing.html` with a live status hero
above the landing's real sections. Since 2026-10 the card is in the landing's language (FV-01): centred,
the primary tone while it works (error and outage keep their own), and "Usually 10 to 30 minutes." on a
line of its own (FV-13). A first sync takes 10-30 minutes and the user has nothing to
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
   `docs/design/gamification-plan.md`'s design intent for this exact moment), then what the
   hunter's history has already earned: the contracts waiting, the Pursuer Level and rank
   claiming them reaches, and the jobs it raises most, from the synced poll's `pursuit` block
   (`sync_views._pursuit_payload`, read only on the synced response). "Claim your contracts"
   leads to Career's claimable board; "Enter your Pursuit" to the Home. With nothing to claim,
   one plain line instead. (It used to show four trophy counts the hunter already had on PSN;
   FV-03.) No auto-reload; the user arrives on the Home on purpose. Quick refreshes and error transitions keep the old reload behavior.

See [home-page.md](home-page.md) for the router, the event contract, and the finalize phases.

## Surface 2: the Career "how it works" modal

`templates/trophies/partials/career/_career_explainer.html`: a first-visit MODAL in the mold of
the old Browse Badges first-run modal (auto-opens once, focus trap, Escape/scrim/Got-it dismiss;
that modal was retired on 2026-10-24, see below), with three beats in
Jeffrey's own words: 25 jobs / five disciplines, Contracts (curated game sets assigned jobs by
genre and theme), and Pursuer (complete contracts to earn XP toward jobs and the overall
level). Jeffrey chose the modal over the earlier inline card ("more in the face" for
information that matters).

- **Server-side auto-open gate**: the modal markup renders for everyone, but `data-auto` (the
  auto-open arm) renders only while `career_explainer` is absent from `request.user.ui_flags`.
- **Reachable forever, unlike badges**: badges never reopen a modal, because their teaching has a
  fuller home at /badges/how-it-works (a second, shorter copy would drift). Browse Badges used to
  greet a first visit with a one-shot modal; since 2026-10-24 (PR-06) it never interrupts, and its
  header's `.pp-edhint` LINKS to that page instead. Career has no long-form page -- the modal IS
  the teaching -- so the summary card's `.pp-edhint`
  ("How does my Career work?", `data-career-open`) reopens it. One copy, no drift. Reopens
  close silently: only an armed (first-visit) dismissal writes the flag.
- **The collage**: below the beats, the three ideas as objects with chevrons between them --
  a quad of real job icons, the shared cover fan `.pp-forge__fan` (a contract IS a set of games),
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
- **Career only, by design**: a Collection explainer was explicitly rejected (the How badges
  work page already owns that teaching moment).

## Surface 3: the PlatPursuit 1.0 launch greeting

`templates/trophies/partials/home/_launch_welcome.html`: a one-shot modal on the Home lobby for
users who existed before the cutover. Three orientation beats (your Career, your Collection, your
new Home) plus a flourish that is theirs: the five disciplines in the Career dossier's own chip
idiom, and a count of the contracts already sitting claimable against their library. It
deliberately does NOT quote a Pursuer level -- XP is banked by CLAIMING contracts, so a
returning hunter is Level 1 here no matter how long their history is, and congratulating them
on a level they have not earned would be the first thing the new site got wrong. No confetti
either: particles are the earn vocabulary, and a redesign is an announcement, not an
achievement.

- **Ships dormant.** `settings.PP_LAUNCH_DATE` (ISO 8601 env var) unset means it never renders.
  Set at cutover, it is the single instant BOTH this modal and the launch announcement email
  compare `date_joined` against, so they can never disagree about who counts as new. They do
  not cover the same POPULATION: the modal needs a linked, synced profile (it lives on the
  lobby), while the email reaches every active account with an address, including people who
  signed up and never linked. The announcement's copy is written to be true for both. A
  malformed value fails boot on purpose: a typo silently disabling the greeting is the failure
  nobody would notice.
- **Existing users only.** Post-cutover signups get the onboarding built for them; a 1.0 welcome
  for someone who never saw 0.x is noise.
- **No reopen affordance**, inverting the Career modal's rationale again: a one-time announcement
  needs no recall, so a flagged user gets a full non-render (the include itself is server-gated).
- **The choreography gate.** The lobby's count-ups, Horizon fills and ring pace would otherwise
  play out behind the modal's scrim on the one visit it exists for. **Moved in 2026-09** when What's
  New made the lobby two modals: the greeting no longer publishes anything, and a gate named after
  one modal is a gate the other cannot use. `_home_modal_gate.html` now arms `ppAfterHomeModal`
  synchronously from the server's flags; `home-motion.js` rides it fail-open; each modal reports back
  through `DetailModal`'s `onSettled` on close, on the typing-guard skip, and on the device-already-
  dismissed path. A 4s backstop covers "nothing ever appeared" and is cancelled via `ppHoldHomeModal`
  the moment a modal opens — an uncancelled deadline released the motion mid-read on every visit.
  See [whats-new.md](whats-new.md#the-lobbys-choreography-gate).
- **Behaviour is `PlatPursuit.DetailModal`** (utils.js) as of the same lane — open, Escape, focus
  trap, the armed-once flag write, the localStorage fallback. It was ~100 inline lines here before.
- **Team preview**: `/?preview=launch-welcome` (staff/moderators), mirroring the landing and
  syncing preview doors.

## The `ui_flags` pattern

`CustomUser.ui_flags` (JSONField, mirrors `browse_defaults`) holds one-shot education flags:
presence of a key means dismissed. Writes go through the quick-settings API's `ui_flag` branch
(`api/user_settings_views.py`, whitelist `UI_FLAGS`), a read-modify-write that preserves other
keys (`career_explainer`, `launch_welcome`). Adding a new one = add its key to `UI_FLAGS`, gate its render server-side, POST on
dismiss.

**Not everything in that dict is a one-shot flag.** `whats_new_seen` also lives there and does not
follow this contract: its value is the **id of the newest announcement dismissed**, it MOVES with every
new entry, and it is written through a separate `whats_new_seen` branch that is *not* in `UI_FLAGS`.
Presence means nothing there — equality does. See [whats-new.md](whats-new.md#the-seen-marker). If you
add a FOURTH shape, document it here too; this section is where people come to learn what the field
means, and it is the one place a separate API branch cannot keep honest on its own.

**The third shape: `contracts_seen`** (Career's new-contracts modal). An ISO-8601 timestamp string,
not a boolean and not an id — the newest `Contract.announced_at` this hunter has been SHOWN. It
moves forward on every dismissal rather than sticking, and like `whats_new_seen` it has its own API
branch and is *not* in `UI_FLAGS`.

It is also the only one of the three with **validation on the stored value**, because every way it
can be wrong breaks the account permanently and nothing in the UI can undo it:

| Rule | Why |
|---|---|
| must parse as a datetime | junk is a 400; an unparseable value already stored reads as "no marker", so the modal returns rather than hiding forever |
| clamped to `now` | a future stamp suppresses the modal for good |
| floored at one year back | an ancient stamp defeats the 14-day first-visit floor, so every `/career/` render sorts the entire announced catalogue for that account |
| never moves backwards | a stale tab dismissed after a newer visit would rewind it and re-show a wave already read |

Doc: [job-board-contracts.md](../design/rebuild/job-board-contracts.md).

**The fourth shape: `challenges_intro_seen`** (the Challenges system intro). A VERSION string, `beta` or
`live`, the newest version of the intro this hunter was SHOWN. Its own API branch, not in `UI_FLAGS`;
validated by `challenges.services.tutorials.merged_intro_marker`: unknown values and any version above
the current one are a 400, and it never rewinds. The per-type Challenges tutorials are ordinary sticky
flags (`challenge_tutorial_<type>`), in `UI_FLAGS` via `tutorials.TYPE_FLAGS`. Doc:
[challenge-systems.md](challenge-systems.md#the-tutorials).

Client discipline (from the retired badge howto + timezone modal prior art):

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
  before the same poll's `platpursuit:sync-progress`. The finale's fill must stay idempotent
  and re-run on the trailing event, which is the one carrying the `pursuit` block.
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
