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
| AC-01 | High | **Badge art is sent at full size to be drawn as an icon.** Every badge image is the uploaded 1000 x 1000 PNG (280 KB to 420 KB), drawn at 46 to 66px. Measured on prod: the fundraiser downloads 17.2 MB of badge art (65 files) of an 18.4 MB page on a laptop and 7.4 MB on a phone, and the network is busy for 28 seconds; `/badges/` spends 3.4 MB of its 4.7 MB on 12 badges; Support 1.4 MB on the hero's five. Nothing generates a smaller size (`BadgeSeries.badge_image`, `Badge.badge_image`: plain `ImageField`s). Every medallion surface pays it. Logged as [SW-06](README.md) | badge image fields, `group_medallion_layers` | fixed: `review/account` (a 512px WebP display copy per piece of art, `BadgeSeries.badge_thumb` / `GroupBadge.badge_override_thumb`, made on save and by `make_badge_thumbs`; `art_layers()` draws it when current, link previews and Discord keep the upload; 25 KB to 50 KB against 340 KB to 420 KB on prod art) |
| AC-02 | Medium | **The fundraiser asks for what it cannot take.** The hero says "Every $10 donated is another badge funded!"; two sections down, "Artworks awaiting a patron" is an empty box: "Every current badge series has a patron." A visitor reads the promise, then that it is not on offer. The tracker beside it says "136 of 137 badges claimed" while nothing is claimable, so one series is counted that nobody can claim: likely a completed claim whose series still has no `badge_image` (counted once as completed and again as needing art), to confirm on prod. Rules 3 and 5 | `fundraiser.html`, `FundraiserView` tracker | fixed: `review/account` (owner: next-wave donations. The hero says every series has a patron and donations fund the next wave, the empty box is gone, a pick is "saved for the next wave"; the tracker counts this campaign's claims plus what is claimable, so it reads N of N) |
| AC-03 | Medium | **The fundraiser is 17,300px tall on a phone** (26 screens; 7,600px on a laptop). "In the queue" is 71 copies of the same placeholder medallion, each saying CLAIMED (6,000px on a phone); "Artwork complete" is 65 tiles (5,000px); the donor wall another 4,200px. The page's job (fund the next badge) sits in the first 1,300px, but the queue says one thing 71 times. Rule 4: the extras stay, behind the job | `fundraiser.html`, `badge_tracker.html` | fixed: `review/account` (totals as one line of facts; the newest twelve artworks then "Show all"; the queue a closed list of names; six claim chips per donor. A 65 / 40 / 3 synthetic campaign: 3,260px on a phone, 1,982px on a laptop) |
| AC-04 | Medium | **Support's checkout is below a phone's first screen.** On a 375 x 667 phone the page opens on the headline and its paragraph; then five badges and four site totals; the plan picker starts at 765px and the join button about 1,250px down. On a laptop the picker sits beside the headline, which is right. Rule 2 | `support/support_hub.html` | fixed: `review/account` (grid areas: lede, box, proof on one column; box beside both on a wide screen) |
| AC-05 | Medium | **Email addresses is the last account page on the retired controls.** `btn` x12, `alert` x4 and `input` x4 on the ratchet allowlist, plus DaisyUI's `radio`, `divider`, `form-control` and `card`: bright filled cyan buttons and a black input, next to a Settings page drawn in `pp-cta` and the house fields. And with one address (the usual case) it offers "Make primary", "Re-send verification" and "Remove" for an address that is already primary, already verified and the only one. Rule 3 | `templates/account/email.html` | fixed: `review/account` (on the auth shell with the house field and `pp-cta`; one form per address offering only what applies; allowlist lowered) |
| AC-06 | Low | **Credits fill a phone.** Support's credits are one name per row on a phone: 3,700px of the 6,460px page, longer than everything above them. Desktop shows five across | `support/support_hub.html` credits grid | not fixed, on purpose: two across cut real PSN names to ten characters (\"Loneso...\", \"Nameles...\"), and a credits wall that cuts the names defeats itself. With the checkout leading on a phone (AC-04), a long list at the very bottom costs nothing |
| AC-07 | Low | **The membership level line truncates on a phone**, where the fact is the point: "PlatPursuit Patron · billed mo..." on the welcome page, "PlatPursuit Cornerstone · member since ..." on My Membership | `support/welcome.html`, `support/membership.html` | fixed: `review/account` (owner: the level name alone, "Patron", at the one source in `marks.mark_style` and the six templates; the lines wrap instead of truncating) |
| AC-08 | Low | **The public roadmap does not line up on desktop.** "In the works" and "Up next" hold three cards each in a four-column grid, so they stop short of the three-across Wishlist row and the full-width call to action below. Its copy carries a double hyphen standing in for a dash ("RetroAchievements -- the whole landscape") | `support/roadmap.html`, `users/constants.py` | fixed: `review/account` (`auto-fit` columns so every tier fills its row; a colon for the double hyphen) |
| AC-09 | Low | **Opening `/logout/` shows a blank white page.** The sign-out URL is POST-only, so a typed or bookmarked visit gets Django's bare 405. allauth's `/accounts/logout/` already redirects home | `plat_pursuit/urls.py` `logout` | fixed: `review/account` (`SignOutView`: a GET goes home and does not sign out; POST unchanged) |
| AC-10 | Low | **My Membership's empty state talks to the team, and offers a switch with nothing behind it.** A hunter who never joined reads "Membership keeps the servers on and the rebuild moving" (the rebuild finished in 2026-09) and gets the "List me on the supporter wall" checkbox, with nothing to list | `support/membership.html` (state `none`) | fixed: `review/account` (copy reworded; the switch shows to anyone wearing a level, a member, or a lapsed member) |
| AC-11 | Low | **The beta gate still wears the old shell**: a DaisyUI `card` with the large logo, where its siblings (the auth pages, the signed-out page) use the house card. Low traffic: nothing links to it while no beta runs | `pages/beta_access_required.html` | fixed: `review/account` (on `account/base_auth.html`) |
| AC-12 | Low | **Settings' "Play celebrations" hint is hard to parse**: "A badge you earn plays on your next My Pursuit page. Off, it waits behind a button with your new platinums." | `users/settings.html` | fixed: `review/account` ("When you earn a badge, its celebration plays by itself the next time you open My Pursuit. Turn this off and it waits for you to tap it.") |
| AC-13 | Medium | **The live campaign promises rewards nobody gets.** Its description (admin data) says "Donations of ANY size receive a special Milestone, Title and Discord role!". The milestone and title grant retired with the legacy milestone engine in 2026-08 (`donation_service.py`), and no Discord role is granted. Found while fixing AC-02 | the `badge-artwork-fund` Fundraiser row | open: owner edit in the admin (deploy checklist, 2026-10-26 step 3) |

## Owner decisions (2026-10-26)

- **The fundraiser (AC-02):** keep taking donations for the next wave. The hero leads with the news; the
  campaign's own description is admin data and still promises "another badge funded", so edit it in the admin.
- **Tier titles (AC-07):** the level name alone ("Patron"), everywhere the mark is named: the Support picker,
  the credits, My Membership, the welcome page and email, Settings, and the title line under names on
  leaderboards and profiles.
- **Changing tier in place:** later, on its own branch (it touches both payment flows and their webhooks).

## Also fixed

- **Four account pages doubled the site name in the tab**: "Settings - PlatPursuit - Platinum Pursuit" (Settings,
  My Membership, the welcome page, Email addresses). `base.html` already appends the name.
- **"PlatPursuit" in Account copy** became "Platinum Pursuit" (Support, the roadmap, Settings, the fundraiser).

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
