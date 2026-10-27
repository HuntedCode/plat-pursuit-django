# Review: Odds and ends

The pages no journey owns: What's new, About, Contact, Privacy, Terms, the arcade (Stellar Circuit), the badge
art reveal event page, the archived reviews page, the goodbye page after deleting an account, and the error
pages (404, 429, and the 400 / 403 / 500 pages that do not exist). Reviewed 2026-10-27 at 375, 1440 and the
owner's laptop (1280 x 633). Method: [README.md](README.md).

**Where:** every page signed out on **https://platpursuit.com** (read-only GETs), and signed in on **dev** with the
owner's saved session. The 404 and 429 templates were also rendered to files for a browser, because dev runs
with `DEBUG` (its 404 is Django's debug page) and nothing can reach the 429 page (OE-01). Dev has no reveal
event, so the reveal page was judged on prod, where the Summer 2026 event is finished.

**Headline:** these pages missed the rebuild. What's new and the goodbye page are in the house style. About,
Contact, Privacy and Terms are the older card-and-prose pages: their headings render as body text because
`prose` does nothing here, and About scrolls sideways on a phone while promising Roadmaps (not live) and a
Trophy Case (deleted). The error pages are the bigger gap: a server error, a tripped rate limit or an expired
form shows Django's bare one-line page, with no site, no way back and no word of what happened. The reveal page
downloads 9.7 MB of art for an event that ended in June.

## Findings

| ID | Sev | Finding | Where | Status |
|----|-----|---------|-------|--------|
| OE-01 | High | **A server error, a refused request or an expired form leaves the site.** There is no `500.html`, `403.html`, `400.html` or CSRF failure view, so each shows Django's bare text ("Server Error (500)", "403 Forbidden", "Forbidden (403) CSRF verification failed. Request aborted.") with no header, no link and no explanation. A 403 is not rare: every tripped `django-ratelimit` limit with `block=True` raises `Ratelimited`, a `PermissionDenied` (67 call sites), and a sign-in form left open past its session fails CSRF. The 429 page (`templates/429.html`, wired as `handler429`) is unreachable: Django never dispatches `handler429`, a fact `challenges/views.py` already records | `templates/` (missing), `plat_pursuit/urls.py` | open |
| OE-02 | High | **About breaks on a phone and describes a different site.** The page is 491px wide on a 375px phone (sideways scroll): the feature cards stay two across with 90px text columns, and the three social buttons run off the edge. It offers "Roadmaps" (not live, owner 2026-10-26) and "Trophy Case" (the page was deleted in 2026-08), and it has no way into the product: no sign up, no link to any feature it names. It is in the sitemap and is what a visitor reads when deciding whether to join | `templates/pages/about.html` | open |
| OE-03 | Medium | **Headings on About, Contact, Privacy and Terms render as body text.** They sit in `prose prose-invert`, but the Tailwind typography plugin is not installed: `.prose` in `output.css` is three DaisyUI colour variables. Every `<h2>` / `<h3>` is the size and weight of a paragraph, so Privacy (4,849px on a phone) and Terms (4,453px) are one undifferentiated wall, and About's "Our Mission" reads as a stray label | `templates/pages/*.html` | open |
| OE-04 | Medium | **The reveal page is heavy and reads as live after the event ended.** It downloads 40 full-size PNGs, 9.7 MB (115 KB to 487 KB each), with 1 of 82 images lazy-loaded, and runs 6,070px on a phone. The Summer 2026 event finished in June (40 of 40), but the page still opens with "Unlock the next piece: every 5 badge platinums reveals the next of 40 artworks". With no event at all it says "No reveal event is running right now. Check back soon!" (page principles rule 3) | `art_reveal/views.py`, `templates/art_reveal/event.html` | open |
| OE-05 | Medium | **The 404 card breaks on a phone.** The joke share card is built with inline fixed widths (140px columns): at 375 the "PLATINUM NOT FOUND" banner is cut to "PLATI", the four stat boxes stack one per row down a narrow column, and the footer line overlaps the card edge. The 429 page copies the same card. One stat is "Earn rate", a number the site retired in 2026-10 | `templates/404.html`, `templates/429.html` | open |
| OE-06 | Medium | **The arcade cannot be played on a phone and is not linked from anywhere.** Stellar Circuit is keyboard-only (no touch handling in `static/js/games/driver/`); on a phone the menu draws at about 4px text and the seed field shows "En". On the laptop the Launch button is below the first screen. Nothing links to `/arcade/stellar-circuit/`, but it has no `noindex` | `templates/minigames/stellar-circuit.html` | open |
| OE-07 | Low | **Contact's email button runs off its card on a phone**, and the page's two cards and "or" divider are the older centred layout | `templates/pages/contact.html` | open |
| OE-08 | Low | **The archived reviews page points at a hub that is gone.** "Back to Community" goes to the home page (the Community hub retired in 2026-08), the buttons are retired DaisyUI `btn`, and the "taking a break" note is from 2026-05. Nothing on the site links to it; only old URLs and search reach it | `templates/trophies/reviews_archived.html` | open |
| OE-09 | Low | **"PlatPursuit" where the brand is "Platinum Pursuit":** goodbye ("Back to PlatPursuit", its tab title), About, Contact, Privacy (twice), the reveal page ("everywhere on PlatPursuit") and the 404 / 429 logo alt text | the templates named | open |
| OE-10 | Low | **What's new stops at 2026-10-18.** Since then: game flag badges, bonus stages and mastery, Hunters opening on Recently Active, average completion as each game's PSN percent | `core/whats_new.py` (entries) | open: content, owner |
| OE-11 | Low | **Privacy and Terms name roadmaps** ("platinum roadmaps", "roadmap contributions") as a live feature. Legal wording, so the owner's call | `templates/pages/privacy.html`, `terms.html` | open: content, owner |

## Checked, not findings

- **Privacy and Terms are current** on what matters: Stripe and PayPal, cookieless Cloudflare analytics, no ads,
  only the session and CSRF cookies, deletion, membership billing and donations.
- **What's new** is in the house style, one job, newest first, and correct at every width.
- **The goodbye page** says what happened and what remains, at every width.
- **The 404's flavour lines** ("500 million trophies") are jokes about PSN at large, not a number the site counts.
- **Load times** are fine everywhere except the reveal page (0.5s to 0.8s on prod for the rest).

## Candidate lessons

- **A class that looks like a plugin is not proof the plugin exists** (`prose`). First sighting.
- **Pages outside every journey miss sweeps**: the rebuild and the 2026-10 reviews both walked journeys, and
  these pages fell between them. Already handled by the rule that every new page gets a slice (README).
