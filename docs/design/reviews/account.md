# Review: Account

The pages a hunter uses to run their account and back the project: Settings and the account pages it links to
(email addresses, password reset, re-authenticate, inactive account, signed out), My Membership and the welcome
page after checkout, the fundraiser, the Support hub with its public roadmap, and the beta-access gate.
Reviewed 2026-10-26 at 375, 1440 and the owner's laptop (1280 x 633). Method: [README.md](README.md).

**Where:** the public pages on **https://platpursuit.com**, signed out (read-only GETs): Support, Roadmap, the
Badge Artwork Fund, password reset (form, "link sent", an expired link), account inactive and the beta gate. The
signed-in pages on **dev**: the owner's saved session for Settings, Email addresses, re-authenticate, Support, My
Membership in all eight preview states and the welcome page in its three; the throwaway no-PSN account for its
Settings, Support and Membership. Interactions that write nothing: the Unlink and Delete dialogs opened and
closed, an empty password change, invalid addresses on the email and reset forms, and the throwaway account
signed out through the avatar menu. Not reachable: the fundraiser's signed-in picker (dev has no campaign) and a
real checkout.

**Headline:** the account pages are in good shape. Settings, the membership page and its states, the welcome
page and every allauth page read as one family, and the Unlink and Delete dialogs are careful. The problems are
the weight and the leftovers. Badge art is sent at 1000 x 1000 to be drawn at 46 to 66px, so the fundraiser
downloads 17 MB of art on a laptop (a site-wide problem; it shows worst here). The fundraiser asks for donations
"to fund a badge" above a box saying there is no badge left to fund, and runs to 17,000px on a phone. The Email
addresses page is the one account page still on the retired DaisyUI controls.

## Findings

| ID | Sev | Finding | Where | Status |
|----|-----|---------|-------|--------|
| AC-01 | High | **Badge art is sent at full size to be drawn as an icon.** Every badge image is the uploaded 1000 x 1000 PNG (280 KB to 420 KB), drawn at 46 to 66px. Measured on prod: the fundraiser downloads 17.2 MB of badge art (65 files) of an 18.4 MB page on a laptop and 7.4 MB on a phone, and the network is busy for 28 seconds; `/badges/` spends 3.4 MB of its 4.7 MB on 12 badges; Support 1.4 MB on the hero's five. Nothing generates a smaller size (`BadgeSeries.badge_image`, `Badge.badge_image`: plain `ImageField`s). Every medallion surface pays it. Logged as [SW-06](README.md) | badge image fields, `group_medallion_layers` | open |
| AC-02 | Medium | **The fundraiser asks for what it cannot take.** The hero says "Every $10 donated is another badge funded!"; two sections down, "Artworks awaiting a patron" is an empty box: "Every current badge series has a patron." A visitor reads the promise, then that it is not on offer. The tracker beside it says "136 of 137 badges claimed" while nothing is claimable, so one series is counted that nobody can claim: likely a completed claim whose series still has no `badge_image` (counted once as completed and again as needing art), to confirm on prod. Rules 3 and 5 | `fundraiser.html`, `FundraiserView` tracker | open (owner decision below) |
| AC-03 | Medium | **The fundraiser is 17,300px tall on a phone** (26 screens; 7,600px on a laptop). "In the queue" is 71 copies of the same placeholder medallion, each saying CLAIMED (6,000px on a phone); "Artwork complete" is 65 tiles (5,000px); the donor wall another 4,200px. The page's job (fund the next badge) sits in the first 1,300px, but the queue says one thing 71 times. Rule 4: the extras stay, behind the job | `fundraiser.html`, `badge_tracker.html` | open |
| AC-04 | Medium | **Support's checkout is below a phone's first screen.** On a 375 x 667 phone the page opens on the headline and its paragraph; then five badges and four site totals; the plan picker starts at 765px and the join button about 1,250px down. On a laptop the picker sits beside the headline, which is right. Rule 2 | `support/support_hub.html` | open |
| AC-05 | Medium | **Email addresses is the last account page on the retired controls.** `btn` x12, `alert` x4 and `input` x4 on the ratchet allowlist, plus DaisyUI's `radio`, `divider`, `form-control` and `card`: bright filled cyan buttons and a black input, next to a Settings page drawn in `pp-cta` and the house fields. And with one address (the usual case) it offers "Make primary", "Re-send verification" and "Remove" for an address that is already primary, already verified and the only one. Rule 3 | `templates/account/email.html` | open |
| AC-06 | Low | **Credits fill a phone.** Support's credits are one name per row on a phone: 3,700px of the 6,460px page, longer than everything above them. Desktop shows five across | `support/support_hub.html` credits grid | open |
| AC-07 | Low | **The membership level line truncates on a phone**, where the fact is the point: "PlatPursuit Patron · billed mo..." on the welcome page, "PlatPursuit Cornerstone · member since ..." on My Membership | `support/welcome.html`, `support/membership.html` | open |
| AC-08 | Low | **The public roadmap does not line up on desktop.** "In the works" and "Up next" hold three cards each in a four-column grid, so they stop short of the three-across Wishlist row and the full-width call to action below. Its copy carries a double hyphen standing in for a dash ("RetroAchievements -- the whole landscape") | `support/roadmap.html`, `users/constants.py` | open |
| AC-09 | Low | **Opening `/logout/` shows a blank white page.** The sign-out URL is POST-only, so a typed or bookmarked visit gets Django's bare 405. allauth's `/accounts/logout/` already redirects home | `plat_pursuit/urls.py` `logout` | open |
| AC-10 | Low | **My Membership's empty state talks to the team, and offers a switch with nothing behind it.** A hunter who never joined reads "Membership keeps the servers on and the rebuild moving" (the rebuild finished in 2026-09) and gets the "List me on the supporter wall" checkbox, with nothing to list | `support/membership.html` (state `none`) | open |
| AC-11 | Low | **The beta gate still wears the old shell**: a DaisyUI `card` with the large logo, where its siblings (the auth pages, the signed-out page) use the house card. Low traffic: nothing links to it while no beta runs | `pages/beta_access_required.html` | open |
| AC-12 | Low | **Settings' "Play celebrations" hint is hard to parse**: "A badge you earn plays on your next My Pursuit page. Off, it waits behind a button with your new platinums." | `users/settings.html` | open |

## Questions for the owner

- **The fundraiser (AC-02).** Every badge series has a patron. What is the page for until the next wave?
  (a) Keep taking donations "for the next wave": lead with that news and say what a donation does now.
  (b) Close the campaign until new series need art, so the page becomes the record of what was funded.
  (c) Something else.
- **"PlatPursuit Patron" (AC-07 and wider).** The tier titles print as "PlatPursuit Contributor / Backer /
  Patron / Cornerstone" in the Support picker, the credits, My Membership, the welcome page, Settings and the
  welcome email. The brand is "Platinum Pursuit". Options: "Platinum Pursuit Patron", just "Patron", or keep the
  short form as a deliberate title. The free copy ("PlatPursuit stays free", "The platinum roadmap for
  PlatPursuit itself") gets the full name either way.
- **Changing tier.** My Membership says that to change level or billing cycle you cancel, wait for the paid time
  to run out, then subscribe again. Both processors can switch a plan in place. Worth building, or does it stay?

## Checked, not findings

- **Every allauth page is one family**: forgot password, link sent, expired link, account inactive, confirm it's
  you and signed out share the auth card, eyebrow, one action and quiet links. Invalid addresses get the
  browser's own message. An empty password change returns to Settings with its errors.
- **The Unlink and Delete dialogs are careful.** Delete asks for the password and a typed phrase, lists what
  stays with the payment processors, and is blocked while a membership is active with a link to cancel first.
- **My Membership's eight states** (active, cancelling, grace, past due, PayPal, PayPal grace, legacy, none)
  each say what is happening and what to do; past due leads with "Update payment method".
- **"Next billing Oct 22, 2026" on the owner's dev membership** is four days ago: dev receives no Stripe
  webhooks, so the test subscription never renews there. Not filed.
- **Support's load event at 7 to 8 seconds** is 58 PSN avatar requests on PlayStation's image servers; the page
  is ready at 1.0s. Not filed.
- **Server time is good everywhere** (0.2 to 0.4s to first byte on prod), with no console errors on any page.

## Candidate lessons

- **An image's file size follows its upload, not its use.** AC-01 is a 1000px upload drawn at 60px. Second
  sighting (any other uploaded image drawn small) would promote it into design-system.md's image rules.
