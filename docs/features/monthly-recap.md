# Monthly Recap System

A "Spotify Wrapped" style feature for trophy hunting activity. Each month, the system generates personalized recaps with trophy stats, activity analysis, badge progress, interactive quizzes, and shareable cards. Recaps are presented as animated slide decks with theme selection and confetti celebrations. Free users get the current + most recent completed month; premium users get full history.

## Architecture Overview

The recap system follows a **denormalization-first** design. All monthly stats are computed once and stored as JSON fields on the `MonthlyRecap` model. Once a month ends, the recap is "finalized" and becomes immutable: even if the user syncs new data, past recaps never change. This guarantees consistent historical snapshots and eliminates re-aggregation costs.

Generation happens **on-demand** (when a user views their recap) or via **cron** (batch generation + finalization). There are no background Celery tasks. The staleness check for the current month regenerates data if older than 1 hour.

**Timezone handling is critical**: all month boundaries are computed in the user's local timezone, then converted to UTC for database queries. A user in Tokyo and a user in New York have different "January" boundaries. The system resolves timezone from `profile.user.user_timezone` (falls back to UTC).

The frontend renders slides via Django template partials fetched one-at-a-time from the API, with per-slide animations, quiz interactivity, and a flavor text system that randomizes descriptive text on each viewing.

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
| `templates/recap/recap_index.html` | Month picker + sync gate |
| `templates/recap/partials/slides/` | 16 slide templates, all built on the `.rcp` shell |
| `components/badge_medallion.html` | Composed by both badge slides (their payloads are frame dicts) |
| `static/css/components/recap-deck.css` | Deck chrome, motion, activity ramp, `.rcp` shell + slide parts |
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
2. `RecapSlideView` validates: month is completed (not current), premium gating for past months
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

1. User clicks share/download button on summary slide
2. `RecapShareImageHTMLView` renders `recap_share_card.html` with cached external images
3. `ShareImageCache` downloads and caches avatars, game icons, trophy icons as temp files
4. Tracks `recap_share_generate` site event
5. For PNG: `RecapShareImagePNGView` renders HTML via Playwright headless browser
6. Client-side tracks `recap_image_download` event on download button click

### Slide Rendering

1. Frontend requests individual slides via `RecapSlidePartialView`
2. API maps each `DECK` beat to a Django template partial in `recap/partials/slides/` (`SLIDE_TEMPLATES`; `test_recap_deck_order` pins that the two sets match exactly)
3. Flavor text system: `SLIDE_FLAVOR_TEXT` dict with random selection per slide type
4. Slides: intro, total_trophies, platinums, rarest_trophy, most_active_day, activity_calendar, games, badges, comparison, summary, 4 quiz types, streak, time_analysis

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
| GET | `/api/v1/recap/<year>/<month>/slide/<type>/` | Yes | - | Individual slide partial |

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
