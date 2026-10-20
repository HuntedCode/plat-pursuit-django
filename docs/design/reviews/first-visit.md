# Review: First Visit

The journey of a brand-new hunter: anon landing, sign up / sign in, email verification, linking PSN,
the first sync, and the first arrival on the lobby and Career. Reviewed 2026-10-20 at 375 / 768 / 1440
(plus 320 for the auth pages), on dev and on the live site. Method: [README.md](README.md).

**Headline:** the journey is technically clean (no console errors, no sideways overflow at any size,
every logged-out page under about one second on dev), but a new hunter crosses between two design generations three
times, and the big payoff moment at the end does not land.

## Findings

Status values: `open`, `planned` (branch named), `fixed` (commit or PR), `won't fix` (with the reason).

| ID | Sev | Finding | Where | Status |
|----|-----|---------|-------|--------|
| FV-01 | High | The journey switches design generations three times: landing to sign up, welcome to link PSN, link PSN to the sync card | See below | planned: onboarding rebuild |
| FV-02 | High | **Bug:** link PSN step 1 drops form errors. An invalid name (`a!`, `ab`) reloads the page with the field cleared and no message | `templates/account/link_psn.html` | fixed: `fix/link-psn-form-errors` |
| FV-03 | High | The first arrival does not pay off: the finale promises a Pursuer and shows tier counts; the lobby says "Welcome back" to a first-timer and buries Career, Collection and claimable contracts | `templates/home/_hero_syncing.html`, `templates/trophies/home.html` | planned: Career + first arrival |
| FV-04 | Medium | Sign up has four names, log in has two | See below | fixed: `fix/review-quick-fixes` |
| FV-05 | Medium | The landing offers two first steps ("Start tracking" and "Create a free account") and never explains the difference | `templates/home/landing.html` | planned: landing |
| FV-06 | Medium | The landing's Profile Card and the two share-card images are illegible on phones: text renders at 2.6 to 6px at 375 (measured on prod) | `templates/home/landing.html` (`.land-card`, proof section) | planned: landing |
| FV-07 | Medium | Sign-up friction: confirm email and confirm password, both passwords wiped on a failed submit, a placeholder that reads as a filled value, and no word about what comes next | See below | planned: onboarding rebuild |
| FV-08 | Medium | "Check your inbox" is a dead end: no address shown, no resend, and the main button goes back to Sign In | `templates/account/verification_sent.html` | planned: onboarding rebuild |
| FV-09 | Medium | Link step 2, the hardest moment, has the least help; the welcome page's copy promises things linking does not deliver | See below | copy fixed: `fix/review-quick-fixes`; the step-2 help is planned: onboarding rebuild |
| FV-10 | Medium | The back-to-top button is the loudest element on every page | `templates/partials/back_to_top.html` | tracked as [SW-02](README.md#site-wide-findings) |
| FV-11 | Low | A brand-new account with no PSN already shows the Premium button and a glowing "NEW" pill | `templates/partials/navbar.html` | planned: site-wide noise |
| FV-12 | Low | The lobby states sync status three times ("Updated ...", "Sync now", the "Synced" dot) | `templates/trophies/home.html` | planned: Career + first arrival |
| FV-13 | Low | On the sync card, the one fact that matters ("10 to 30 minutes") is mid-way through an italic paragraph of up to six lines | `templates/home/_hero_syncing.html` | planned: onboarding rebuild |
| FV-14 | Low | The Career explainer's middle illustration is three blank cover cards | `templates/trophies/partials/career/_career_explainer.html` | open, needs a design call: the fan is a deliberate pure-CSS idiom ("no assets") shared with the badge how-to and How badges work, so changing it changes three surfaces |
| FV-15 | Low | Error styles differ: landing uses an outlined box with a lone red dot, the auth pages use salmon-filled blocks | `static/js/landing.js`, `templates/account/*.html` | planned: DaisyUI split |
| FV-16 | Low | The landing's badge section renders its heading and button over an empty shelf when the showcase is empty (cold cache, or a failure's negative cache) | `templates/home/landing.html`, `core/services/landing_service.py` | fixed: `fix/review-quick-fixes` (the section now hides. Accepted side effect: the heartbeat band's wash then meets Career's `--alt` wash, two washed bands in a row, on a cold cache only) |
| FV-17 | Low | Forgot password lacks the logo header the other auth pages carry | `templates/account/password_reset.html` | won't fix: misread. Only sign in and sign up carry that header; the other nine auth pages match forgot password. Its yellow tagline is an FV-01 marker, so whether any auth page keeps it belongs to the onboarding rebuild |
| FV-18 | Low | At 320px the landing eyebrow wraps to two lines and strands its tick on the left | `templates/home/landing.html` (`.land-eyebrow`) | fixed: `fix/review-quick-fixes` |
| FV-19 | Low | **Latent bug**, found while fixing FV-02: if starting verification raises, step 2 re-renders without `verification_code` or `profile`, so the hunter sees an empty code box. The step 1 exception message also reads "An error occured" | `trophies/views/profile_views.py` (`LinkPSNView.post`, the `verify` branch's `except Exception`) | fixed: `fix/review-quick-fixes` |
| FV-20 | Low | **Latent**, found by the FV-19 audit: every return to step 2 (`LinkPSNView._back_to_step_two`) shows the stored code as-is, even after its one-hour expiry, where step 1 regenerates an expired code. It also never re-checks that the profile is not linked to someone else (the outage path had the same gap before FV-19) | `trophies/views/profile_views.py` | planned: onboarding rebuild |

### FV-01: two design generations

The new generation (eyebrow + display headline, `pp-cta` buttons, `--pp-*` surfaces) covers the landing,
the no-PSN welcome hero, the lobby and the Career explainer. The older generation covers:

- `templates/account/signup.html`, `login.html`, `password_reset.html`, `verification_sent.html`, and
  the shared `templates/account/snippets/auth_header.html`
- `templates/account/link_psn.html` (all three steps)
- the status card and finale in `templates/home/_hero_syncing.html`

Its markers: a yellow italic tagline ("No Trophy Can Hide From Us"), long italic paragraphs, solid
bright-cyan DaisyUI `btn-primary` buttons, a numbered stepper, a blue info box, and salmon-filled error
blocks. The sync card's yellow is DaisyUI's `warning` token (`border-l-warning`, `progress-warning`,
`badge-warning`), so a healthy first sync is dressed as a warning. On phones these pages also carry the full footer (about two
screens) and the tab bar under a short form, so a focused flow keeps offering exits.

Likely shape of the fix: one onboarding flow (auth, link, sync card) rebuilt together, design pass
first per the [rebuild playbook](../rebuild/rebuild-playbook.md).

### FV-03: the first arrival

- The finale reads "Your Pursuer has emerged" and then shows four tier counts, which the hunter already
  has on PSN. No level, top job, or Pursuer appears.
- `templates/trophies/home.html` renders the "Welcome back" eyebrow unconditionally, so the first visit
  after "Enter your Pursuit" greets a newcomer as a returning user.
- The lobby leads with PSN-style trophy statistics; Career and Collection sit in two small cards at the
  bottom.
- The most rewarding first action, claiming contracts the hunter's history already satisfies, appears
  only as the "9+" count on My Pursuit.

### FV-04: one action, several names

| Action | Names in use | Where |
|--------|--------------|-------|
| Create an account | "Create a free account" | landing hero + close |
| | "Register" | account menu, `templates/partials/navbar.html` |
| | "Sign Up" | auth pages, footer |
| Log in | "Log in" | landing, account menu |
| | "Sign In" | auth pages, footer |

### FV-07: sign-up friction

- `ACCOUNT_SIGNUP_FIELDS` asks for `email2` and `password2`, although the password fields carry a
  show-password toggle.
- A failed submit clears both password fields (standard Django), so every mistake costs a full retype.
- The placeholder `cool.guy@platpursuit.com` (in `signup.html`, `login.html`, `password_reset.html`)
  renders at a similar weight to a typed value.
- Nothing on the page says the next steps are "verify your email, then link PSN with a code in About Me".

### FV-09: link step 2 and the copy around it

- Step 2 asks the hunter to leave the site and edit About Me on PSN, with no link to where that is done
  and no note that the code expires after one hour (`VerificationService.generate_code`).
- `templates/home/_hero_no_psn.html` promises a "six-digit code"; the code is six hex characters
  (`A3F2B1`).
- The same hero says "We'll walk you through" the Gaming History privacy setting; linking repeats the
  warning but gives no steps.

## What works

- The landing's hero copy and its proof sections (live heartbeat, medallion shelf with the inspect
  modal, the ratings carousel).
- The no-PSN welcome hero: one clear job, a three-step preview, the privacy warning up front.
- The sync room's per-state closing copy ("Paused, not lost." and friends).
- The Career explainer's copy: three beats, plain language.
- The landing's client-side PSN-name check: plain, specific error copy.

## Checked, not findings

- **"2,375" twice on the live landing** (trophies earned in the last 24h, and games rated): separate
  queries in `core/services/site_heartbeat.py`, a coincidence.
- **Empty badge shelf, missing heartbeat band, Sekiro-only ratings on dev**: cold dev caches. The live
  site renders all three. (The empty-shelf presentation itself is FV-16.)
- **The syncing preview opening on "Synced!" with a full bar, and finale numbers that disagree with the
  greeting**: preview-door and simulate-panel artifacts, not reachable by a real first sync.

## Candidate lessons (promote on a second sighting)

Not rules yet. If another slice shows the same thing, promote it into
[design-system.md](../../reference/design-system.md) Gotchas, or into a test where one can enforce it.

- A flow keeps one design generation from its first page to its last.
- Every form renders its field and non-field errors, and keeps what the user typed. (Testable.)
- A scaled-down desktop card is not a phone layout; nothing renders below a minimum effective text size.
- First-run states have first-run copy ("welcome back" is earned).
- One name per action across the site. (Testable as a string guard.)
- Nothing competes with a page's one job: no upsell or "new" marker before the hunter has linked PSN.
