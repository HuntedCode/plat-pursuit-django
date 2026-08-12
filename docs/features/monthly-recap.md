# Monthly Recap System

A "Spotify Wrapped" style feature for trophy hunting activity. Each month, the system generates personalized recaps with trophy stats, activity analysis, badge progress, interactive quizzes, and a shareable card. The recap is presented as a full-screen **ceremony** -- an entrance cover, then a paced 20-beat deck that closes on the card. **Every logged-in hunter can open any month they earned a trophy in**; the premium ladder was deleted and no month is gated. The in-progress month stays closed, because a Wrapped is a retrospective.

## Architecture Overview

The recap system follows a **denormalization-first** design. All monthly stats are computed once and stored as JSON fields on the `MonthlyRecap` model. Once a month ends, the recap is "finalized" and becomes immutable: even if the user syncs new data, past recaps never change. This guarantees consistent historical snapshots and eliminates re-aggregation costs.

Generation happens **on-demand** (when a user views their recap) or via **cron** (batch generation + finalization). There are no background Celery tasks. The staleness check for the current month regenerates data if older than 1 hour.

**Timezone handling is critical**: all month boundaries are computed in the user's local timezone, then converted to UTC for database queries. A user in Tokyo and a user in New York have different "January" boundaries. The system resolves timezone from `profile.user.user_timezone` (falls back to UTC).

The frontend renders slides from Django template partials fetched in ONE request (`/deck/`), with per-beat motion, quiz interactivity, and a flavor text system that varies descriptive text per viewing. One request per beat meant ~20 calls against a 60/min all-API throttle, which 429d the rest of the site when a hunter flicked between months.

## File Map

| File | Purpose |
|------|---------|
| `trophies/services/monthly_recap_service.py` | Main business logic: generation, slides, finalization (~1,543 lines) |
| `core/services/monthly_recap_message_service.py` | Shared context for emails and notifications (178 lines) |
| `trophies/recap_views.py` | Page views: RecapIndexView, RecapSlideView (194 lines) |
| `api/recap_views.py` | API: available months, detail, regenerate, share cards, slide partials (748 lines) |
| `core/management/commands/generate_monthly_recaps.py` | Batch generation + finalization (208 lines) |
| `core/management/commands/send_monthly_recap_emails.py` | Email + notification sending (360 lines) |
| `core/services/email_service.py` | Reusable HTML email sender via SendGrid |
| `static/js/monthly-recap.js` | MonthlyRecapManager: slides, animations, quizzes, themes (~1,100 lines) |
| `templates/recap/monthly_recap.html` | Main slide presentation page |
| `templates/recap/recap_index.html` | **The landing page**: latest month + the archive, by year |
| `templates/recap/partials/slides/` | 20 slide templates, all built on the `.rcp` shell |
| `components/badge_medallion.html` | Composed by both badge slides (their payloads are frame dicts) |
| `static/css/components/recap-deck.css` | Motion, activity ramp, the `.rcp` slide shell + its parts |
| `static/css/components/recap-stage.css` | The entrance cover and the full-screen stage (`.rcx`) |
| `static/css/components/recap-archive.css` | The landing page: hero, month tiles, timezone row |
| `static/js/utils.js` | `PlatPursuit.takeover()` -- scroll lock, page-recede, focus trap, Escape |
| `templates/recap/partials/recap_share_card.html` | Share card HTML (landscape/portrait) |
| `templates/emails/monthly_recap.html` | Non-spoiler teaser email with CTA |

## Data Model

### MonthlyRecap
- `profile` (FK), `year`, `month`: `unique_together`
- Trophy aggregates: `total_trophies_earned`, `bronzes_earned`, `silvers_earned`, `golds_earned`, `platinums_earned`
- Game stats: `games_started`, `games_completed`
- Highlight data (JSONField): `platinums_data`, `rarest_trophy_data`, `most_active_day`, `activity_calendar`, `streak_data`, `time_analysis_data`
- Quiz data (JSONField, denormalized): `quiz_total_trophies_data`, `quiz_rarest_trophy_data`, `quiz_active_day_data`, `badge_progress_quiz_data`
- Badge stats: `badge_xp_earned`, `badges_earned_count`, `badges_data`
- Comparison: `comparison_data` (vs_prev_month_pct, personal_bests)
- Status: `is_finalized`, `email_sent`, `email_sent_at`, `notification_sent`, `notification_sent_at`
- Timestamps: `generated_at`, `updated_at`
- Three indexes: `(profile, year, month)`, `(year, month, is_finalized)`, `(profile, is_finalized)`

Immutable pattern: once `is_finalized=True`, regeneration is skipped even with `force_regenerate=True`.

## Key Flows

### On-Demand Generation

1. User navigates to `/recap/<year>/<month>/`
2. `RecapSlideView` validates: the month is completed (not current, not future) and has trophy activity. No premium gating -- see the intro.
3. API call to `RecapDetailView` fetches recap data
4. `MonthlyRecapService.get_or_generate_recap()` checks for existing recap
5. If none or stale (current month + >1 hour old): calls `generate_recap_data()`
6. Service collects 90+ data points: trophy counts, game stats, activity calendar, streaks, time-of-day analysis, badge XP, quizzes, comparisons
7. All data denormalized into MonthlyRecap JSON fields via `update_or_create`
8. `build_slides_response()` converts model into slide array for frontend

### Batch Generation (Cron)

1. `generate_monthly_recaps --finalize` runs on 3rd of month at 00:05 UTC
2. Finds all profiles with trophy activity in the target month (±14 hours for timezone edge cases)
3. Generates recap for each profile, marks `is_finalized=True`
4. Separate `send_monthly_recap_emails` runs at 06:00 UTC (7 hours later)

### Email Sending

> **OFF since 2026-08.** `settings.MONTHLY_RECAP_SEND_ENABLED` defaults to `False` and
> `send_monthly_recap_emails.handle()` returns immediately, so **no email and no in-app notification** goes
> out while the recap is being rebuilt. The notification is dispatched from inside the email loop
> (`_send_emails` calls `_send_recap_notification` on both the success and the failure branch), so the two
> cannot be stopped separately without lifting it out -- stopping both is the intent for now. `--dry-run`
> still previews. The recap PAGE is unaffected. Re-enable via the environment when the rebuilt email ships;
> the Render cron is paused as well.


1. `send_monthly_recap_emails` finds finalized recaps with `email_sent=False`
2. Checks email preferences via `EmailPreferenceService` (skips opted-out users)
3. Builds context via `MonthlyRecapMessageService.build_email_context()`
4. Sends via `EmailService.send_html_email()` with `log_email_type='monthly_recap'`
5. Marks `email_sent=True`, `email_sent_at=now()`
6. Independently sends in-app notification to ALL users (ignores email preferences)
7. Marks `notification_sent=True`, `notification_sent_at=now()`

### Share Card Generation

1. The ceremony fetches the card's HTML as it opens and mounts it in the card scene, so the closing beat
   hands over to a card that is already there rather than to a spinner
2. `RecapShareImageHTMLView` renders `recap_share_card.html` with cached external images
3. `ShareImageCache` downloads and caches avatars, game icons, trophy icons as temp files
4. Tracks `recap_share_generate` site event
5. Download presses `RecapShareImagePNGView`, which renders that HTML via Playwright headless browser.
   The button is `PlatPursuit.CardDownload` (idle -> busy -> done), NOT a navigation -- see the stage
   notes below for why that distinction is load-bearing inside a takeover
6. Client-side tracks `recap_image_download` on the press, which is intent to save rather than a
   completed save (a retry fires it again)

### Slide Rendering

1. Frontend requests individual slides via `RecapSlidePartialView`
2. API maps each `DECK` beat to a Django template partial in `recap/partials/slides/` (`SLIDE_TEMPLATES`; `test_recap_deck_order` pins that the two sets match exactly)
3. Flavor text system: `SLIDE_FLAVOR_TEXT` dict with random selection per slide type
4. The deck is a data-driven `DECK` of 20 `RecapBeat`s, each with a `when` condition, not an append sequence. Order and membership live in `monthly_recap_service.py`; `test_recap_deck_order` pins it.

### The landing page (`/recap/`)

**It used to be a redirect.** `RecapIndexView.get` bounced you into your most recent month whenever a
recap existed, falling back to the current one, so the page only ever RENDERED for a hunter with no
trophy activity at all. Two consequences, both of which are why this got rebuilt:

- the archive was unreachable from its own URL, the one the nav and footer both point at;
- a **second** month picker (a 12-cell year calendar) had to live at the bottom of the recap page to
  compensate -- two pickers, drifting independently, neither at the address people were sent to.

It leads now, and it is shaped as a **record** rather than a picker: the latest month as an object you
can pick up, then every month you have earned in, stacked by year, each carrying what it actually held.
The duplicate calendar and its `month-selector.js` are gone.

| Piece | Notes |
|---|---|
| Hero | The newest openable month. Trophy total counts in; Begin / Watch it again reflects `has_been_viewed`. |
| Archive | Free content (no outer card) per the stacked rule. `staggerReveal` on the same grammar as the browse grids. |
| Tile | Month, trophy count and platinum count at EQUAL weight, plus a "New" flag when unwatched. |
| Colour | Unwatched = the brand cyan (edge + labelled pill). Platinums = `--color-trophy-platinum`. |
| Timezone | A header button opens the prompt. That is the ONLY timezone control on the page. |

**The first-run prompt.** `user_timezone` is `default='UTC'` and non-null, so it cannot tell a London
hunter who never touched it from one who deliberately chose UTC -- and that is exactly the population a
timezone prompt is for. `CustomUser.timezone_confirmed_at` answers that and nothing else: null means never
answered, and only an explicit save sets it (including a save that picks the same zone back -- confirming
UTC is an answer).

It opens by itself only when **both** are true: the server says never confirmed, and this device has not
dismissed it (`localStorage['pp.tz.prompt.dismissed']`). The two answer different questions, which is why
both exist -- the server flag is durable and cross-device, the local one is "not right now" and
deliberately per-device, so dismissing on a phone does not silence the prompt on a desktop forever. The
header button opens it on purpose, always.

It is a **confirmation, not a picker**: the browser already knows the answer, so it shows the detected zone
and asks "right?", with the full list behind "Pick a different one" for VPNs, travellers and shared
machines. Where `Intl` is unavailable it falls straight through to the list, because a confirm button with
nothing to confirm is a dead control. The prompt renders for gate-free hunters *including those with no
months yet* -- that hunter is the most likely never to have set a zone, and the one whose first recap gets
mis-filed if it is wrong.

The zone list, browser detection and save path live in `static/js/timezone-picker.js`, shared by the
prompt and the inline row. The list is data, and data duplicated across two files diverges quietly.

One control, not two. The header button states the current zone and opens the prompt; there is no second
`<select>` anywhere on the page. A utility row used to sit at the foot doing the same job, which left one
setting with two controls that had to be kept in step and no answer to which was the real one. The button
names the city rather than the full zone -- "America/New_York" does not fit a 375 header and a bare clock
icon says nothing.

Three DB-aggregated reads back the whole page, and none of them scale with how much history a hunter has:

- `months_with_activity` -- which months exist (`TruncMonth` in the hunter's own zone).
- `month_activity_totals` -- the same grouping plus trophy type, giving each tile its numbers. The count
  was **already being computed and discarded**: `months_with_activity` runs `Count('id')` purely to force
  the GROUP BY. Adding the type turns ~12 rows a year into ~48.
- `months_already_seen` -- one bounded read. Absence of a `MonthlyRecap` row IS "unseen", because rows
  are created BY opening a month, so nothing needs joining.

`get_or_generate_recap` is deliberately NOT called here. Generating a recap for every month someone
merely glanced at is work nobody asked for, and on a whale it is the expensive kind; opening a month
still generates it. `test_recap_archive_page` pins the render, the figures, and that the query count does
not grow with the number of months.

Two colours sit close together here and it is deliberate. "Unwatched" is the brand cyan and the
platinum figure is `--color-trophy-platinum` (#67d1f8), which are adjacent hues. Trophy-type colour is
fixed vocabulary across the whole site, so a platinum count in a page-specific hue would be the worse
inconsistency -- and the two never share a role: the state is carried by an edge and a pill with the
word "New" on it, never by a number. If they ever do read as one thing, move the STATE (to
`--pp-accent`, say), not the platinum.

### The share card

**A sibling of the plat card, not a copy.** Someone who has seen one should recognise where the other came
from before reading a word, so everything carrying the identity is shared verbatim with
`shareables/plat_card.html`: the ground gradient, both inner scrims, the Frame, the `40px 46px` padding,
the identity strip and its brand block, the `rgba(64,72,83,0.55)` hairline, the tier-dot colours
(`TIER_DISPLAY`), and the two-voice type. `test_the_card_shares_the_plat_cards_identity` compares against
the plat card FILE rather than copied literals, so neither can drift alone.

What differs is the subject, so what differs is the middle. A plat card is about one game; this is about a
month, so **the month leads** (not the trophy count -- a card that opens with a number is a stat readout,
and nobody posts a stat readout) and the evidence is a spread rather than a single cover.

| Zone | Carries |
|---|---|
| Header | Avatar, username, "Monthly Recap", and the brand block. Constructed exactly as the plat card's. |
| Body | Two equal-height **panels**: the month, its tier dots and three figure cells on the left; the **activity calendar** on the right. |
| Footer | A titled **platinums container** (covers + game names, 6 slots then "+N"), the rarest find, then best day and badges at the far edge. |

Constraints worth knowing before editing it:

- **Landscape only, 1200x630.** Both endpoints used to accept `image_format=portrait`, which renders this
  fixed composition into a 1080x1350 viewport: clipped right, two thirds empty below. They reject it now.
- **Inline styles, hex only.** Playwright renders via `set_content()` in an `about:blank` origin, so there
  is no stylesheet and `var(--pp-*)` resolves to nothing. The token values are ported by hand at the top
  of the template and pinned by `test_no_css_custom_properties_reach_the_card`.
- **Only two font families exist here.** The renderer embeds Bricolage Grotesque and Inter from
  `static/fonts/`; anything else silently falls back.
- **It is seen at ~450px wide in a Discord embed** (~37%), which is what sets the type scale -- the same
  constraint the plat card documents. Check any change at that size, not just at 1200.
- **The activity calendar earns its space.** It is the one element that makes the card read as a MONTH
  rather than a total: a figure says how much, the grid says how it happened, and a fortnight's binge
  and a steady thirty days can share a trophy count without being the same month. Dropping it in the
  first pass is what made the card feel barren. Its ramp (`CALENDAR_RAMP` in `api/recap_views.py`) is
  a hand port of `.activity-level-*` from `recap-deck.css` -- climbing in size AND colour so it never
  depends on hue alone -- because `color-mix()` against `--pp-*` resolves to nothing in the renderer.
  A platinum day takes a warm ring rather than more colour: level 4 is "busy", the ring is "you closed
  something out", and the ring is deliberately off the ramp's hue or it vanishes on exactly the days
  most likely to be level 4.
- **Emptiness was the failure mode, and CONTAINERS were the fix.** Three iterations read as barren or
  scattered. The first two tried to solve it by moving content into the holes, which only moved the
  holes -- the card was bare text floating on a ground, so every rearrangement produced a differently
  shaped gap. What worked was giving the body real surfaces: two equal-height panels, so the space
  between them is a gutter rather than a hole, and the figures in their own cells so three numbers read
  as a set rather than as items sharing a line. `scratchpad/voids.py` maps empty regions by hit-testing
  a grid of points; useful for confirming, but note it diagnoses WHERE the space is, not what to do
  about it -- the answer was structural both times.
- **The platinum container answers three questions at once.** Its title says what the covers ARE (bare
  art is context-less), its count says how many, and each cover carries its game's name. Six slots,
  down from eight, because a name needs the width the eighth cover was using; the rest become
  "+N". At **one** platinum it still reads deliberately -- title, count of 1, one named cover. At
  **zero** the container is dropped entirely: an empty box titled "Platinums earned" is worse than
  no box, and the footer's other blocks spread into the space.
- **The grounds are the plat card's curated eight**, server-rendered as `.pc-theme` swatches **inside the
  card scene** -- under the card they change, above the buttons that act on it. They spent one commit in
  the since-removed below-fold panel, which rendered directly beneath the entrance and whose preview stayed
  empty until the card had been opened -- so eight swatches sat on the intro screen with nothing to apply
  themselves to. ("Change the look" went with that move: it existed to close the ceremony and land you on
  a picker elsewhere.) The real gradient is the swatch, and choosing one repaints **every** mounted card,
  which was a real bug back when there were two of them and an id lookup found only the panel's, leaving
  the card in front of the hunter unmoved. A freshly mounted card also takes the current choice, since the
  HTML arrives carrying the template's own ground. The listeners are wired in `setupEventListeners` rather
  than anywhere that waits on the deck prefetch: card-only can open before that lands. The page previously shipped all ~110 site
  gradients into a `window.GRADIENT_THEMES` global feeding a `<select>` and a colour-grid wall --
  the same thing the plat card's rebuild moved away from. `applyBackground` reads `--pc-theme-bg`
  off the checked swatch rather than a JS registry, so the thing clicked and the thing that paints
  are one value, and the PNG endpoint resolves the same key server-side.
- **Consistency lives in the SKELETON, not the content.** Every card shares one set of coordinates:
  header, both body panels, the footer, the three figure cells and the calendar all land at
  identical positions and sizes whether the month produced 0 platinums or 12. Two things make that
  true, and both were bugs first:
  - **The footer has a fixed height (176px).** The body is `flex: 1`, so it absorbed whatever the
    footer did not use -- and the footer's height depended on whether the platinum container
    existed, that being its tallest element. A month with none got body panels 347px tall instead
    of 255px and its dividing rule 92px further down the card.
  - **The figure row always has exactly three cells.** It was two or three depending on whether a
    platinum landed, which changed the shape of the most-read zone. Each slot now falls back to
    something that happened (`_figure_cells`): platinums or active days, games or the longest
    streak. Never a printed zero -- a zero in 40px type states an absence in the largest thing on
    the card -- and only the platinum cell ever takes the accent, so a fallback never dresses up
    as a boast.
- **Game names wrap to two lines, in a FIXED two-line box.** Clamping alone would leave "Hades" one
  line tall and "Ghost of Tsushima" two, so covers in a row would sit at different heights and the
  container's height would depend on which games a hunter happened to finish -- the drift the fixed
  footer exists to stop. The second line costs ~14px, which came out of the container's padding and
  two pixels a cover rather than out of the footer height (the calendar needs every pixel it has: a
  31-day month starting on a Saturday runs to SIX rows).
- **All three footer columns carry their height.** Two-line game names set the footer at 176px, but
  the other two columns were still one short row each -- 68px and 46px of a usable 161. Both fill it
  now, using payload the card had never shown: the rarest trophy's **rarity grade** ("Ultra Rare",
  which is what makes a bare 1.4% mean anything, carried in `rarity_label` since launch) and the
  month's **dominant genre** from `taste_data` -- the one stat here that says something about the
  hunter rather than about the numbers. The stats became a two-column grid so four of them use the
  height rather than pushing the card wide; the rarest find beside them needs that width more.
- **No placeholder cover slots, ever.** Padding the container to a fixed six so every card looks
  identical was considered and rejected: an empty slot advertises what the hunter did NOT do, and
  six is a display limit rather than a target. The same call the plat card already made on its DLC
  pill, which only ever ADDS. A sparse month's space is filled with things that HAPPENED instead --
  the longest streak (stored since launch, shown nowhere until now) and the rarest trophy's GAME,
  both widest exactly when the platinum container is absent. Measured across 0/1/3/6 platinums, the
  footer's largest internal gap went from 466/370/310/82px to 64/138/108/30px.
- **Zero figures are dropped, never printed.** A row of zeroes is a worse card than a shorter row, and
  nobody should be talked out of sharing by their own card.

### Presentation: the Entrance and the Stage

The recap is a **ceremony**, not a dashboard panel. Direction chosen from three built at
[`/design/recap-stage/`](../../templates/design/recap_stage_workshop.html); the other two (a PS-era
"broadcast" readout, and a scroll-driven editorial "spread") remain on that page.

The page used to be a breadcrumb plus three nested `card bg-base-200/90 border-2 border-base-300`
wrappers around a fixed-height box with a row of dots and two ghost circle buttons -- anti-reference #1 in
its purest form. It is now:

- **The Entrance** (`.rcx-enter`): a cover, not a header. The month, its headline numbers, one action, and
  a "Just get the card" shortcut for people who came back for the artifact rather than the story.
- **The Stage** (`.rcx`): a full-screen takeover, rendered hidden on page load so the deck prefetch
  finishes while the cover is being read and entering is instant.

**The three regions (30 / 40 / 30).** The stage is split left-to-right into *back*, *pause*, *forward*.
One number states it (`ZONE_EDGE` in the controller, pinned to the stylesheet's zone and wash widths by a
test), because the regions you can hit and the regions you are shown drifting apart is invisible in both
files and shows up only as clicks landing somewhere the wash did not promise.

| Region | Width | Tap | Shown by |
|---|---|---|---|
| Back | 0-30% | Previous beat | Left arrow + an accent wash across the region |
| Pause | 30-70% | Latches / unlatches the pause | A pause glyph on aim; a play glyph while latched |
| Forward | 70-100% | Next beat | Right arrow + an accent wash across the region |

Why it is not a two-way split: everything that was not the backward edge used to advance, which made the
middle -- the part you are reading -- a forward button, and left holding a finger down as the only way to
pause. That is the wrong gesture for exactly the beats that need it, the dense ones. The middle now
latches, so a tap that lands there because you misjudged the edge pauses rather than skipping something.

The washes exist because the arrows are pinned at the extreme edges while the regions run 30% inward:
they said which direction a click would go and nothing about how far that side extended.

A wash is masked to transparent at its top and bottom. Its box is `.rcx__stage`, which begins directly
under the top bar and (on a manual beat) ends directly above the Continue row, so a flat `top: 0` /
`bottom: 0` drew a hard line along both seams -- the tint appeared out of nothing mid-screen and read as
the background being clipped rather than as a region lighting up. The horizontal gradient is what carries
the boundary; the vertical mask only stops the shape having an edge nobody asked for.

The latch has four rules, and each of them was a way for a "paused" deck to start moving again:

- `releaseBeat` returns early while pinned, so lifting the finger does not end it.
- `startBeatTimer` returns early while pinned. Every resume path funnels through it (a deferred release,
  a dialog closing, a quiz being answered), so this is the only place that has to know.
- `armBeat` clears it, because moving on is what un-pauses the deck -- and a beat that arrived already
  pinned would look broken rather than paused.
- `visibilitychange` respects it: returning to the tab restarts a *running* beat, never a paused one.

A beat with no clock cannot be paused; it is already waiting on you, and "paused" there would be a state
with nothing suspended. That covers quizzes and the calendar, and also **reduced motion**, where no beat
is ever armed and the deck moves only when the hunter moves it -- there the middle zone goes inert, the
hint drops its pause clause and the control leaves the tab order, because an affordance for a control that
does nothing describes behaviour the hunter will then not get. Note also that `holdBeat` freezes the bar's *visual*
unconditionally but only recomputes the remainder when a timer is running: the latch arrives on `click`,
by which point `pointerup` has already released the hold, so there may be no clock left to stop but there
is always a bar mid-flight to catch.

**Below the fold there is nothing**, and the month page is the shorter for it. A share panel used to sit
there -- its own preview, its own scaler, its own download button -- revealed by closing the ceremony. So
finishing the recap dropped you back on the intro screen with a second copy of the card underneath it: the
entrance offering to start something you had just finished, above the thing you had just been shown. One
surface shows the card now, and it is the one the ceremony ends on.

**Closing the ceremony leaves the page, and does not close first.** `data-exit` carries
`{% url 'recap_index' %}` and `leaveForArchive()` navigates straight there, by every route out -- Done,
the close X, Escape, and from the card-only route as well. The month page's only job is to start the
ceremony, so returning to it afterwards puts the hunter in front of a button they have just finished
pressing; the archive is where the next choice (another month) actually lives.

Closing *first* was the version before this, and it read as two exits for one intent: the stage lifts
away, the page behind it un-recedes and paints, and then the navigation replaces all of it a moment
later. Now the ceremony simply holds until the archive paints -- one move, and honest, because the page
is not gone, it is going. The stage is left standing on purpose: its timers stop, but everything a
teardown would tidy is about to be discarded by the page load anyway, and tidying it visibly IS the jump.

Escape is takeover's, so `takeover()` grew `opts.onDismiss` -- called instead of `close()`, and
deliberately after its `dialog[open]` deferral so dismissing the platinum modal still does not dismiss
the surface. Any takeover where being dismissed does not mean being torn down can use it.

Note this applies to bailing MID-deck too, a deliberate simplification: one exit that always means the
same thing, rather than a rule about when you pressed it.

A "More Months & Settings" section used to follow the panel, holding a duplicate month picker; the archive
carries that and the timezone now, so the entrance's aside links there instead.

Load-bearing details, each of which was a bug first:

- **The chrome outranks the card scene.** `.rcx__card` is `z-index: 3` and takes `inset: 0` in card-only
  mode, so it covered `.rcx__top` entirely -- the close X was on screen, looked live, and every click on
  it landed on the card while the stage stayed open. `elementFromPoint` on the button returned
  `recap-card`. The bar carries `z-index: 4` now.
- **The ending HANDS OVER; it does not share the stage.** The summary used to shrink into the end screen's
  header, and that one decision produced every bug this transition ever had. Sharing meant splitting the
  stage into two bands and then arguing the header's layout box down to fit its half:

  - Its body was only faded, so it kept the slide's full height -- the visible half floated above ~80px of
    dead air and the invisible half sat behind the card. The chips were never dismissed, just covered.
  - The base slide is `overflow-y: auto` so a dense beat can be scrolled to. Boxing the summary into its
    band made the content taller than the box, so the closing line arrived **in a little scroll well with
    a visible thumb**.
  - `transform: scale()` shrinks what you SEE while the layout box stays full size, so on a shorter stage
    the header still ran past its band and the title landed on the card's label -- caught at 700px, where
    the band is only 180px tall.

  Each fix was real and each exposed the next, which is the tell. Now the summary simply **leaves**
  (`rcxBow`: fade with a 4% recede, reading as the beat stepping back) and the card scene owns the whole
  stage carrying **its own** mark and title (`.rcx__card-head`). One object arrives instead of two
  negotiating, and nothing has to be dismantled child by child on the way out. Verified at 900 / 760 / 700
  / 620px and at 375 / 390 / 768 wide: header, card, grounds and buttons stack in order, none overlapping,
  all inside the stage.

  The same header is why **both routes end identically**. "Just take me to the card" has no summary to
  borrow a header from, so before this it landed on a bare card; now it reaches the same screen.

- **The end screen starts below the bar, not at the top of the stage.** `--rcx-bar` is declared once on
  `.rcx` and used by both `.rcx__top` (as `min-height`) and the full-stage card scene (as its top inset) --
  the bar is in flow while the scene is absolute, so the scene cannot measure what it is clearing and a
  second hardcoded number would drift. Without it the card-only route, where the card is largest and the
  column tallest, centred its header *into* the bar's row: measured header top 5, bar bottom 59, the mark
  sitting behind the close X. Clearing it in the inset rather than nudging the header keeps the scene
  centred in the room it actually has, and `fit()` reads that box -- so the card scales down to suit
  (630 -> 578px at 900) instead of running off the bottom.

- **The card scene is a scene ON the stage, and is fitted after it gets it.** Two bugs, one shape.

  It was a SIBLING of `.rcx__stage`, positioned against the whole surface, so it had to be *told* where
  the chrome ended -- a `--rcx-bar` token the top bar and the scene both read. Right on desktop, wrong on
  a phone: the progress timer sits above the bar and pushes it ~17px further down, so the header centred
  into the bar's row and the mark sat behind the close X. Moving it inside `.rcx__stage` makes `inset: 0`
  mean the stage, correct at every breakpoint with no number. (Safe because the controller only ever
  removes `.recap-slide` children from that container, never its innerHTML. It did mean the swipe handler
  needed the `is-ending` guard the tap zones already had -- the card now lives inside the swipe container,
  so most swipes land on it, and scrubbing the deck from the end screen is not a thing.)

  And the card is mounted while the scene is still waiting offstage at `height: 62%`, so the first `fit()`
  measured a box less than half the one it ends up in: the card arrived at **scale 0.45** and any stray
  resize silently doubled it to 0.90. `showCardScene` re-fits in the same breath as the class that changes
  the room -- reading geometry there forces a synchronous layout including the new class, so the card is
  sized once, before it is ever painted at the wrong size. 283px -> 565px tall at a 900px stage.

- **The Download button is shared, and it had to be.** The ceremony's was a bare
  `window.location.href`. That cannot show progress on a call that runs headless Chromium, cannot name the
  file, and on a failure the browser has already left -- so a render error or the 20/m rate limit replaced
  the whole ceremony with a JSON error document and no way back. It now runs on
  `PlatPursuit.CardDownload` with the plat card modal (and, until it was removed a commit later, the
  below-fold panel -- the three of them had three copies of fetch-blob-anchor with three different ideas
  of what a slow press looks like).

  Two things are specific to the stage. It opts out of the success toast (the toast host is `z-50` and the
  stage is `z-90`, so one fired from inside renders behind the ceremony) and carries its own
  `#recap-dl-error` line instead -- which is an in-flow sibling of the card frame, so showing it re-fits
  the card, the same trap the plat card modal hit and only on the failure path. And `--pp-dl-accent` points
  the busy tint at the beat's accent rather than the site primary, since the whole surface is keyed to it.

  `recap_image_download` moved with the button. It hung off the below-fold panel, which was the only way
  to get the card when it was written; leaving it there would have quietly zeroed the metric as the
  ceremony took over.

- **A component stylesheet outranks Tailwind's `hidden`.** Utilities live in a layer and these component
  files do not, so an unlayered `display: flex` beats `.hidden { display: none }` whatever the source
  order. Three classes needed guards and each failed differently:
  `.rcs-state` (both terminal states rendered on every month page that loaded fine),
  `.rcs` (latent -- the share panel was empty on the paths that hid it, so it cost only a stray margin;
  that panel is gone now and the guard went with it),
  and `.rcx`, which was the bad one. The stage is `position: fixed; inset: 0; z-index: 90` and merely
  TRANSPARENT when idle, and it is dismissed two ways: the `hidden` attribute (which worked) and the
  `hidden` class, used by the no-activity and error paths (which did not). So a recap that failed to load
  put an invisible full-screen sheet over its own error state, swallowing every click including Try again
  -- `elementFromPoint` on that button returned `.recap-slide` rather than the button.
  Any component class that sets `display` and gets toggled needs a `.cls.hidden { display: none }` guard.
  `test_no_component_class_overrides_the_hidden_utility` checks this generally, and scans BOTH the markup
  and runtime `classList.add('hidden')` calls -- its first version read only `class=` attributes, which is
  exactly how `.rcs` and `.rcx` slipped past a test written to catch them.
- **The share preview's scaler is anchored on a NAMED class** (`.rcs__frame`), not a utility. It finds its
  box with `closest(...)` and bails silently when it finds nothing, so when it was anchored on `.relative`
  and the panel was rebuilt, the 1200px card would have sat unscaled in a 600px frame with no error.

- **`.rcp` DISSOLVES on the stage.** In a takeover the stage IS the frame, so a bordered card inside one
  is a frame within a frame. The shell keeps its real job (type scale, rhythm, anatomy) and stops drawing
  a box; the type grows to carry the beat instead.
- **The stage moves to `<body>` on open.** A `position: fixed` element inside the transformed page-recede
  wrapper is positioned against that wrapper, not the viewport.
- **`renderAllSlides` may remove only `.recap-slide` elements.** The stage also holds the tap zones, the
  direction arrows and the hint line; `innerHTML = ''` destroyed all of it on first render, which is why
  the arrows were never in the DOM and the zone buttons the controller had captured were detached nodes.
- **Only ONE clock may run.** Pacing is derived per beat (reading time + a beat per thing to look at,
  clamped 4-9.5s) and armed through `armBeat`. The quiz's post-answer dwell goes through the same path:
  when it had its own `setTimeout`, two timers raced and how early the deck jumped depended on how fast
  the question was answered. The **summary** broke this rule a second time and in the same shape: it
  opted out of `startBeatTimer` (being the last slide) and ran a private `_cardTimer` to hand over to the
  card, so nothing governing the real clock reached it -- a held finger did not stop it and a latched
  pause left the deck reporting "paused" while the card arrived underneath on schedule. The last beat now
  runs the same clock as every other and `endOfBeat` decides what expiry means: advance, or hand over.
- **Beats that wait on the hunter show an EMPTY pulsing segment.** `.is-live` declares `width: 100%` and
  relies on a transition to get there, so removing the transition *commits* to full rather than freezing.
  A quiz showing a completed timer is a lie about a beat that has not run.
- **`syncBeatState` runs before `paintBars`.** The bar needs to know whether THIS beat is paused; when the
  state was updated afterwards, the slide following a quiz was painted as "waiting" and its bar never ran.

### The deck arc (`DECK`)

Slide order is **data**, not control flow. `MonthlyRecapService.DECK` is an ordered list of `RecapBeat`
(type, `when`, `payload`), and `build_slides_response` walks it. It used to be ~110 lines of
`if ...: slides.append(...)`, so the arc could only be read by tracing branches.

The arc is **open -> build -> peak -> payoff -> close**:

| | |
|---|---|
| Open | `intro` |
| Build (how much) | `quiz_total_trophies` -> `total_trophies` -> `games` |
| Build (when, how consistently) | `quiz_active_day` -> `most_active_day` -> `activity_calendar` -> `streak` -> `time_analysis` |
| Peak (what it was worth) | `quiz_rarest_trophy` -> `rarest_trophy` -> `platinums` |
| Peak (what it moved) | `quiz_closest_badge` -> `badges` -> `comparison` |
| Payoff + close | `quiz_score` -> `summary` |

Two editorial rules are pinned by `test_recap_deck_order.py`:

- **Every quiz sits immediately before the thing it asks about.** Guess, then find out. Insert a slide
  between a quiz and its reveal and the pairing breaks silently.
- **The peak comes after the build.** Platinums used to be the *fourth* slide, spending the deck's biggest
  moment before it had built anything.

`quiz_score` is the payoff, and the only beat with **no server payload**: `RecapQuizManager.getScore()`
has always been computed and never shown, so the controller fills the slide from the answers actually
given this sitting. It is dropped when the deck contains no other quiz -- and the check must exclude
itself, since `quiz_score` also starts with `quiz_` (a naive prefix test always found "a quiz" and shipped
"0 / 0 guessed right" into months with nothing to grade).

**Payloads must stay JSON-serialisable** -- the deck array is serialised into the response. Template-only
shapes (the calendar's `first_day_offset` range) are built in `_build_slide_context`, not in the beat. A
`range` in a payload 500s every recap page.

### The slide shell (`.rcp`)

Every slide is one `.rcp` element. Before the rebuild each of the 16 hand-rolled the same outer centring
flex plus a `card bg-white/[0.03] border border-base-content/5 ...` with slightly different padding, so no
two slides shared a frame. A Wrapped reads as *authored* because every beat shares a frame and only the
CONTENT changes, so the shell owns the frame, rhythm and type scale, and the slides own nothing else.

| Part | Role |
|------|------|
| `.rcp__kicker` | The lead-in ("You earned"). Quiet, sets up the reveal. |
| `.rcp__figure` | The one thing the slide is about. Usually a `.pp-tally` number. |
| `.rcp__caption` | What the figure means ("trophies this month"). |
| `.rcp__body` | Supporting detail: a breakdown, a list, a grid. |
| `.rcp__flavor` | The closing line. Last, quiet, optional. |

Modifiers: `--hero` (intro/close/peak), `--wide` (grids), `--bleed`, and the accent swaps
`--accent-secondary` / `--accent-warm` / `--accent-success`. **A slide changes its whole colour story with
one modifier**: every part inherits `--rcp-accent` from the shell, so no part carries its own colour.

Shared parts (`.rcp__stamp`, `.rcp__chips`, `.rcp__pair`, `.rcp-quiz__*`, ...) all sit in
`recap-deck.css` below the shell. Composition over restatement: tier cells are `.scard` with
`--scard-accent`, figures are `.pp-tally` + `data-countup` -> `PlatPursuit.countUp`.

## API Endpoints

| Method | Path | Auth | Rate Limit | Purpose |
|--------|------|------|------------|---------|
| GET | `/api/v1/recap/available/` | Yes | - | List available months |
| GET | `/api/v1/recap/<year>/<month>/` | Yes | 60/min | Full recap with slides |
| POST | `/api/v1/recap/<year>/<month>/regenerate/` | Yes | 10/min | Force regenerate (current month only) |
| GET | `/api/v1/recap/<year>/<month>/html/` | Yes | 60/min | Share card HTML |
| GET | `/api/v1/recap/<year>/<month>/png/` | Yes | 20/min | Share card PNG (Playwright) |
| GET | `/api/v1/recap/<year>/<month>/deck/` | Yes | 30/min | **Every slide's HTML in one response** (what the deck uses) |
| GET | `/api/v1/recap/<year>/<month>/slide/<type>/` | Yes | 60/min | One slide partial. No in-repo caller; kept as a public surface |

## Integration Points

- [Token Keeper](../architecture/token-keeper.md): Sync freshness gate requires sync within current calendar month for most recent recap
- [Badge System](../architecture/badge-system.md): Badge XP earned and badge progress quiz data from `UserBadgeProgress`
- [Notification System](../architecture/notification-system.md): `monthly_recap` notification type, sent independently of email
- [Email System](../guides/email-setup.md): SendGrid via EmailService, EmailLog tracking, EmailPreferenceService opt-out
- [Share Images](share-images.md): Playwright renderer, ShareImageCache for external image caching
- `MonthlyRecapMessageService`: Shared context builder ensures email and notification content consistency

## Gotchas and Pitfalls

- **Source order decides in `recap-deck.css`, and losing is silent.** Two separate fixes in this file were
  dead CSS that read as working, both caught only by rendering: the reduced-motion `.stagger-item` restore
  (see below) and the platinum grid's mobile `-webkit-line-clamp: 1`, which was declared in a later
  override that lost to the base rule at equal specificity, so cover names wrapped and spilled their
  column. Put the value in the rule it belongs to, or put the override immediately after its base.

- **`countUp` overwrites `textContent` wholesale.** A sign or unit inside a `data-countup` node is eaten
  mid-animation. Templates put them in sibling `.rcp__sign` / `.rcp__unit` elements; pinned by
  `test_recap_deck_contract.py`.

- **The rarest-trophy slide speaks PSN's rarity vocabulary, not the site's.** `earn_rate` is
  `Trophy.trophy_earn_rate` (share of players who own the game), while `data-rarity` / `.pp-rarity` grades
  against the whole PlatPursuit community. Different populations. The slide reuses `RARITY_LABELS` from
  `completion_card_service` and styles the band as a quiet `.rcp__stamp` so it cannot be mistaken for the
  site's scale.

- **The recap reads the badge subsystem, never the legacy tables.** `UserBadge` / `UserBadgeProgress`
  are written only by `badge_service`, which no live path calls (evaluation runs through `badge_apply`
  from the `evaluate_badges` command), so the slides that read them were showing an empty or frozen set
  for everybody. Both badge readers now use `UserGroupBadge` and `SeriesBadgeStanding`, and their
  payloads ARE Medallion frame dicts, so both templates compose `components/badge_medallion.html`.
  Pinned by `test_recap_deck_contract.py`.

- **The three badge fields are snapshots of a NON-STATIONARY derivation.** `badge_xp_earned`,
  `badges_earned_count` and `badges_data` are sealed at generation (a finalized recap is never
  recomputed -- `get_or_generate_recap` returns early on `is_finalized`, before it even consults
  `force_regenerate`, and the admin regenerate action skips finalized rows). But unlike
  `total_trophies_earned`, whose inputs are stable history, these depend on the badge CATALOG, which is
  mutable: author a new series and a hunter's old plays retroactively clear stages that did not exist
  then. Because generation is on-demand, **the value depends on when the hunter first opened that
  month** -- two hunters with identical 2019 activity get different frozen numbers if a series shipped
  between their visits. Accepted deliberately: the number is always true as of when they looked, and a
  Wrapped is a keepsake, not an audit ledger. The alternatives are worse (recomputing rewrites people's
  saved recaps; clamping to `badge.created_at` puts XP in months where the earned badges do not appear).

- **Monthly badge XP comes from the engine's dates, not a ledger.** There is no badge-XP ledger and
  none is needed: every cleared gating stage carries `StageResult.base_date` and every earned badge
  carries `GroupBadgeResult.earned_date`, so `badge_xp.monthly_xp` buckets the same two components
  `_group_badge_xp` sums. **That coupling is load-bearing** -- change how XP is scored and both must
  move, or the recap and the profile's standing show different numbers for the same work.
  `test_badge_monthly_xp.py` pins the reconciliation (buckets sum to the scored total, short only by
  clears that have no date to be placed by).

  Attribution is by COMPLETION date, not badge creation date: a 2016 platinum is credited to 2016 even
  if the series was authored in 2025. That matches `UserGroupBadge.earned_at`, so a slide's earned count
  and its XP always agree. (The legacy `StageCompletionEvent` clamped retroactive credit to
  `badge.created_at` instead; matching it would put XP in months the earned badges do not appear in.)

  Cost: one evaluation per recap GENERATION -- ~6 catalog queries (profile-independent) plus the two
  bounded, whale-safe completion reads. `get_or_generate_recap` persists the result, so the deck's 8-16
  concurrent slide requests read the stored number, not the engine.

- **The closest-badge quiz only appears on the most recent completed month.** `SeriesBadgeStanding` is
  live state with no history. Now that every month is openable, generating an old recap would freeze
  TODAY'S progress into it and label it with that month, permanently, because the snapshot is persisted.
  Older months return `None` and the frontend drops the slide.

- **Quiz answered-state is PER SLIDE and derived from `quizResults`, not a flag.** One shared
  `hasAnswered` boolean plus an `initQuizSlide` that only runs on a slide's first visit meant: answer
  quiz A, advance to quiz B (which reset the flag), go back to A -- and A's own answer no longer counted,
  so navigation locked on a slide the hunter had already answered. Both quiz paths record the result
  BEFORE rendering feedback, because the recorded result *is* the flag.

- **The document-level arrow-key handler must keep yielding.** To form fields and contenteditable (the
  share section below the deck has a `<select>`), to `dialog[open]` (the platinum detail owns the
  keyboard while up), and to modified presses.

- **`.has-overflow` has to be re-checked on resize.** The deck is `clamp(520px, 100vh - 360px, 720px)`,
  so which slides overflow moves with the viewport. `visualViewport` is watched too: mobile browser
  chrome collapsing changes `100vh` without firing `resize`.

- **The platinum-day detail is a native `<dialog>`, and its content is escaped.** The hand-rolled overlay
  it replaced registered a document-level Escape handler and unbound it only on the Escape path, so
  closing by backdrop or X leaked one dead handler per open; and it interpolated `game_name` /
  `trophy_name` -- PSN-sourced text -- straight into `innerHTML`. `showModal()` brings the top layer,
  backdrop, focus trapping and Escape with it. Note that overriding `display` on the dialog drops the
  UA's `inset: 0; margin: auto` centring, which is why the rule restates it.

- **Three DOM contracts couple templates, controller and stylesheet**, and none of them raise when broken:
  `data-countup`, `.rcp-cal__cell` / `--plat`, and the quiz's `data-quiz-*` plus the `is-correct` /
  `is-wrong` / `is-dimmed` / `is-locked` / `is-selected` state classes. All pinned by
  `tests/engine/test_recap_deck_contract.py`.

- **Reduced motion must remove MOVEMENT, never content or behaviour.** Two bugs lived here: `.stagger-item` rests at `opacity: 0` and is revealed BY its animation, so `animation: none` left every platinum card, badge row and calendar day permanently invisible; and the calendar's platinum-day click handlers were registered inside `animateCalendarSlide`, which the preference skips -- so it removed a feature, not just its motion. **The restore must be declared AFTER the `opacity: 0` rule**: the first fix put it in the main reduced-motion block higher up the file, where equal specificity meant the base rule won and the fix silently did nothing. Caught by rendering with the preference on, not by reading. Pinned by `test_recap_reduced_motion.py`.
- **Timezone conversion edge case**: Month boundaries in UTC may not align with user's local calendar month. Solution: convert boundaries from user's local midnight to UTC using `pytz`, with ±14 hour buffer for batch queries.
- **Finalized lock**: Once `is_finalized=True`, the recap will NOT regenerate even with `force_regenerate=True`. This is intentional for data immutability.
- **Quiz data insufficiency**: Each quiz type needs minimum 2-4 options. Returns None if too few items (e.g., user only played 1 game). Frontend skips quiz slides with None data.
- **Activity threshold**: Zero trophies in a month means no recap is created (row not inserted, not an empty recap).
- **Notification vs email preferences**: Emails respect `EmailPreferenceService` opt-out. In-app notifications are sent to ALL users regardless. This is intentional.
- **Staleness check scope**: Only applies to the current (incomplete) month. Past months are immutable once finalized.
- **Badge progress quiz**: Uses `UserBadgeProgress.last_checked` as proxy for "earned by month end". This is a denormalized snapshot, not a live query.
- **Share card image caching**: `ShareImageCache` downloads external images (PSN avatars, game icons) to temp files. These are ephemeral and re-fetched as needed.
- **No premium gating (2026-08).** Every month a hunter earned a trophy in is theirs to open. A recap is a record of what someone did; charging to look back at your own history was the wrong thing to sell. The gate was duplicated in five places (four in `api/recap_views.py`, one in `trophies/recap_views.py`) plus the templates and `month-selector.js`; all removed.
- **The month list comes from TROPHY ACTIVITY, not stored recap rows** (`months_with_activity`). This is the one that unlocked history: rows are created BY opening a month, so a picker sourced from rows never offered a month that had no row, so it was never opened, so it never got a row. `TruncMonth` in the hunter's own timezone, DB-aggregated, one row per month.
- **The current month is still closed** (page 404s it). A live month is a stats lookup, which is the opposite of the experience. It is also no longer listed -- the old free-tier list contained *only* the current month, i.e. exclusively the one month the page refuses to open.
- **Cron timing**: Generate recaps at 00:05 UTC on 3rd, send emails at 06:00 UTC on 3rd. The 7-hour gap allows generation to complete before emails fire.

## Management Commands

| Command | Purpose | Usage |
|---------|---------|-------|
| `generate_monthly_recaps` | Batch generate + finalize recaps | `python manage.py generate_monthly_recaps --finalize [--year Y --month M] [--profile-id ID] [--dry-run]` |
| `send_monthly_recap_emails` | Send emails + notifications | `python manage.py send_monthly_recap_emails [--year Y --month M] [--profile-id ID] [--dry-run] [--force] [--batch-size 100]` |
| `test_email_system` | Preview recap email | `python manage.py test_email_system user@example.com --recap-preview` |

## Related Docs

- [Share Images](share-images.md): Playwright rendering, image caching
- [Email Setup](../guides/email-setup.md): SendGrid configuration, email preferences
- [Cron Jobs](../guides/cron-jobs.md): Recap generation and email timing
- [Notification System](../architecture/notification-system.md): monthly_recap notification type
