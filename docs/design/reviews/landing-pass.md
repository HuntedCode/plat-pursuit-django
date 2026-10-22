# Landing: design pass (fix plan branch 7)

Status: **built** (2026-10-21, `feature/ui/landing-pass`). Covers FV-05 and FV-06. Judged from fresh captures
of the anonymous landing at 375, 768 and 1440 on dev.

## The one idea

The landing has one first step, and it is the search: type a PSN name, see your history. Signing up is what
you do once you have seen yourself, so the search should hand you to it rather than compete with it. And
everything the landing shows off should be readable on the device most visitors hold.

## FV-05: one first step (decided, owner 2026-10-21)

Today the hero offers "Start tracking" (the search) and "Sign up free" side by side, and never says how they
differ.

- **The button reads "Look up".** It does exactly that: finds a PSN name and pulls its history. Nothing is
  "tracked" for the visitor until they sign up.
- **A found profile offers the claim.** On its own line under "Open the profile": **"That's you? Sign up
  free to claim it."** Only when nobody has an account on the profile (the search and status endpoints gain a
  `claimable` flag), so nobody is invited to claim someone else's profile.
- **The claim carries the name through sign-up.** The link is sign-up with `next=/accounts/link-psn/?psn=<name>`.
  allauth keeps `next` on the pending login and resumes it when the email is confirmed in the same browser,
  so the hunter lands on Link PSN with the name already filled in. Sign-up also keeps the name in the session
  (`users/services/claimed_psn.py`), so it is still waiting when allauth's copy is not: a confirmation more
  than 15 minutes later, a resend, or "Wrong address? Fix it". In another browser the confirmation lands on
  sign-in, and the name is typed again on Link PSN. Linking reuses the profile the search synced
  (`LinkPSNView` `get_or_create`), so the claim starts no second sync.
- **The "Sign up free · Sign in" line stays** under the hero, for visitors who came to sign in.

## FV-06: legible on a phone (owner chose option 1, 2026-10-21)

Measured at 375: the Profile Card renders at 0.28 scale, so its labels are 2.8 to 4px (its biggest number
18px). The two share-card images are 343px wide (0.29 of 1200), with the same result. At 768 the card's labels
are 6px; at 1440, 8px. These are 1200x630 share images by design, dense on purpose, so they cannot simply
re-flow the way a page does.

Options:

1. **Captions plus a full-size view** (recommended). Each image keeps its place as a picture of what you get,
   with a short, legible caption naming what is on it, and a tap opens a full-screen viewer at real size
   that pans and pinch-zooms. The image stops pretending to be text; the caption does that job.
2. **Swipe through it at a readable size.** On phones the card shows at about half size in a horizontal
   strip that snaps to its three sections (Trophy record, Career, Collection). Readable-ish (labels about
   5px) and a lot of swiping.
3. **A phone-native mini card.** Rebuild the card's key numbers as a phone layout. Needs the showcase data
   cached alongside its HTML, and becomes a second design of the card to keep in step.
4. **Leave it.** It is a picture of a share image, and its job is the impression.

## As built

- **The claim's copy.** An unclaimed profile found by name reads "Found SomeHunter." and then the claim line;
  "Sign in to refresh them" is kept for a profile someone already owns. Two account pitches in one breath
  was one too many.
- **`claimable` is `profile.user_id is None`**, the link flow's own rule (`_claimed_by_someone_else`); `user` is
  only set when verification succeeds, so a profile mid-link stays claimable, as Link PSN would. An
  `account_id` gate was tried and removed: a name PSN does not know is normally deleted when its first sync
  fails (`Profile.set_sync_status('error')`), so an empty `account_id` almost always means "still queued", and
  the gate hid the claim from real hunters who searched twice while the queue was busy. The exception is a first
  sync cut short by a PSN outage (set `synced`, not deleted): a typo searched then stays claimable until the
  unregistered sweep, and Link PSN's own lookup still refuses it.
- **The session's claimed name belongs to the account that claimed it.** On a shared browser the claimer may
  confirm on their phone and never sign in here; the next person to sign up or sign in must not find the name.
  "Fix it" deletes the claiming account, so it releases the record and the next sign-up adopts it; only that
  account's own Fix it does, so a different person fixing their typo on the same browser cannot inherit it.
- **Link PSN's prefill** reads `?psn=`, else the session's claimed name, and is used only when `LinkPSNForm`
  itself accepts it; anything else is dropped silently. The claim link carries the server's lowercased name.
  The session's name is forgotten once step 1 is submitted.
- **allauth's pending login lasts 15 minutes** (`ACCOUNT_LOGIN_TIMEOUT`, allauth's default, not raised here: it
  governs every login stage). A later confirmation lands on sign-in rather than signing the hunter in; the
  session's claimed name is what still fills Link PSN in.
- **The viewer** is a native `<dialog>` (focus trap, Escape and the inert page come with `showModal()`). Its
  stage is the scroller; the item keeps 1200x630 and centres by `margin: auto`, so an overflowing item starts
  at its left edge instead of being clipped out of reach. The Profile Card is copied from the page (the
  server-rendered card has no ids or controls); a share image reuses the page's own pick (`currentSrc`, the
  WebP, a tenth of the PNG). The "View full size" button sits under each caption: on the picture it covered
  card content in every corner. The picture opens the viewer too, and a press on the stage's empty space
  closes it (the dialog is full screen, so there is no backdrop). The buttons ship `hidden` and JS reveals
  them, so without JS or `<dialog>` there is no dead button (or zoom cursor). The hint names the pointer's
  gesture: "Drag" for touch; for a mouse, the arrow keys or a sideways scroll, since a plain wheel scrolls
  vertically and the item overflows sideways.
- **The note under the Profile Card** names what is on it: "A sample Profile Card: platinums, trophy
  record, Pursuer rank and badges on one card."

## Not in this branch

- The no-JS search (the form posts to a JSON endpoint). Pre-existing, noted for later.
