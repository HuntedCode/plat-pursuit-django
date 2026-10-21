# Onboarding rebuild: design pass (fix plan branch 5)

Status: **built** (2026-10-21, `feature/ui/onboarding-rebuild`), with the three owner decisions below. Covers FV-01,
FV-07, FV-08, FV-09 (step-2 help), FV-13, FV-20, the auth half of FV-15, and the house form fields.
Judged from fresh captures of every page at 375, 768 and 1440 (sign up, sign in, forgot password, check
your inbox, the no-PSN welcome, link steps 1 to 3, the sync card and its finale).

## The one idea

Onboarding is one journey, so it should look like one: from "Sign up" to "Your Pursuer has emerged", every
screen uses the language the landing and the no-PSN welcome already speak (eyebrow, display headline,
`pp-cta`, `--pp-*` surfaces). Today the middle of the journey (auth, link PSN, the sync card) is the older
generation: a yellow italic tagline, bright solid buttons, a blue info box, salmon error blocks, and a
first sync dressed in DaisyUI's warning yellow.

## What changes, by screen

### 1. One auth shell (FV-01, FV-17's leftover)
The 12 auth templates in `templates/account/` share one layout partial instead of each repeating the card
(`email.html`, the address management page, belongs to the Account slice):

- A narrow card on a `--pp-*` surface, opening with the landing's eyebrow and a display headline
  ("Sign up", "Welcome back", "Check your inbox").
- The logo block and the yellow tagline ("No Trophy Can Hide From Us") go. The navbar already carries
  the logo, and the tagline was the loudest FV-01 marker.
- Buttons become `pp-cta` (full width on phones); text links become the house link style.
- Errors render as house notices (`.pp-notice`), not salmon `alert` blocks (FV-15's auth half).

### 2. Sign up (FV-07)
- One email, one password (owner decision). The password keeps its show toggle and strength meter.
- The placeholder `cool.guy@platpursuit.com` goes; a label plus an empty field reads as empty.
- One line under the button says what comes next: "Next: confirm your email, then link your PSN."
- The email the hunter typed survives a failed submit (it already does); the password is the only retype.

### 3. Check your inbox (FV-08)
- Shows the address the email went to.
- **Resend**, under allauth's own 1-per-3-minutes limit, with the cooldown shown on the button.
- **"Wrong address? Fix it"**: returns to sign up with the address prefilled, and removes the account
  that was just made with the wrong one (see decision B).
- The main button stops pointing back to Sign in; the spam-folder hint stays.

### 4. The no-PSN welcome
Already in the new language. Its three-step preview and the privacy warning stay. Only copy that the link
pages contradict is touched.

### 5. Link PSN (FV-09 step-2 help, FV-20)
- Same shell as auth. The numbered stepper becomes a quiet "Step 2 of 3" eyebrow; the blue info box
  becomes the same warning row the welcome page uses.
- **Step 2 gets real help:** where About Me lives on the PlayStation App and on PS5 (exact menu paths,
  checked against Sony's help pages during the build), how to set Gaming History to "Anyone", that the
  code expires in an hour, and that the code can be removed once linked.
- **FV-20:** returning to step 2 regenerates an expired code, as step 1 does, and re-checks that the
  profile has not been linked to someone else in the meantime.
- Step 3's verifying bar uses the house progress bar.

### 6. The sync card (FV-13, FV-01)
- Rebuilt in the land-hero language, with the finale's centred layout, so the card and the finale it
  turns into are one object.
- A healthy first sync is no longer a warning: the accent is the primary colour. Error and outage keep
  their own tones.
- The one fact that matters leads, as a line of its own: **"Usually 10 to 30 minutes"**. The italic
  six-line paragraph becomes two short plain lines.
- Every hook `syncing.js`, `navsync.js` and `landing.html`'s inline mirrors read stays (`data-sync-*`,
  `home-sync-*` ids, `data-pursuit-*`); the source pins in `test_sync_onboarding.py` already guard them.

### 7. House form fields (SW-01 pending row)
Built for these pages, then used everywhere new:

| House | Replaces | Notes |
|-------|----------|-------|
| `.pp-field` | `form-control` + label + error wrapper | label, control, hint, error in one block; error via `aria-invalid` + `aria-describedby` |
| `.pp-input` | `input`, `select`, `textarea` | one box for all three; password reveal as a built-in slot |
| `.pp-check` | `checkbox` | sign in's "Remember me" is the first consumer |
| `.pp-progress` | `progress` | the sync bar and link step 3 |

`toggle`, `radio` and `range` have no onboarding consumer, so they stay pending until a page needs them
(YAGNI). The four retired classes go behind the existing ratchet guard (decision C).

## Owner decisions (2026-10-21)

- **A. Focused chrome on auth and link pages.** Hide the footer and the phone tab bar there (the navbar
  stays, so the logo still goes home). Today a phone carries about two screens of footer and a tab bar
  under a short form. **Decided: hide them.**
- **B. "Wrong address? Fix it" deletes the just-made account.** Only the session that created it, only
  while it is unverified and has never signed in. Without it, the mistyped address stays taken by an
  account nobody can reach. **Decided: delete it.**
- **C. Field retirement scope.** Ratchet (today's ~150 uses allowlisted, new uses refused, old ones
  convert as pages are touched) or convert every page now. **Decided: ratchet**, as `btn` was.

## As built

- **Check your inbox** reads one session record (`users/services/pending_signup.py`), written by the
  sign-up itself (or a sign-in to a different unconfirmed account) and stamped by every confirmation email
  that really sends, never for a signed-in sender (a staff resend from the admin), and cleared by a
  completed sign-in. allauth's own "Confirmation email sent to X." notice is dropped for visitors only
  (`CustomAccountAdapter.add_message`): the page says it already, while Manage email addresses, signed in,
  still needs it.
- **No new account oracle:** sign up already says "This email is already in use." for an existing address
  (`CustomUserCreationForm.clean_email`), so only new accounts ever reach "Check your inbox". Resend and fix-it take no address from the request, so they can only act on the
  sign-up that happened in that browser. Resend goes through allauth's own send, so its one-per-three-
  minutes limit still holds; the page shows the wait instead of failing silently.
- **Fix it** deletes the account only while it is unverified, has never signed in and is not staff,
  clears allauth's pending login for it, and returns to sign up with the address prefilled (`?email=`,
  allauth's own prefill).
- **Link PSN step 2:** the PS5 privacy path ("Users and Accounts > Privacy > Who can see your gaming
  history") is from PlayStation's support pages; the PS5 profile path (avatar > Profile > Edit Profile >
  About) is from a third-party guide, as Sony documents no About path. Re-check both if Sony moves them.
- **Hooks unchanged:** the sync card keeps every hook `syncing.js`, `navsync.js` and `landing.html`'s
  inline mirrors read.
- The password reset form keeps its second password field: it is allauth's form, and the sign-up
  decision was about sign up.

## Found along the way (not fixed here)
- The Django admin's "add user" page 500s (`'CustomUserForm' object has no attribute 'instance'`) on
  main too: `CustomUserAdmin.add_form` is the allauth sign-up form, which is not a ModelForm. Fixed on
  its own branch (`fix/admin-add-user`): the admin now has its own form, `users.forms.AdminAddUserForm`.

## Not in this branch
- The landing (FV-05, FV-06): branch 7.
- The Account slice's own pages (email management, Settings).
- The auth emails' styling.
