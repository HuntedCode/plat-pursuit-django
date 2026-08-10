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
| `templates/trophies/monthly_recap.html` | Main slide presentation page |
| `templates/trophies/recap_index.html` | Month picker + sync gate |
| `templates/trophies/recap/partials/slides/` | 18 slide templates (intro, stats, quizzes, etc.) |
| `templates/trophies/recap_share_card.html` | Share card HTML (landscape/portrait) |
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
2. API maps 18 slide types to Django template partials in `recap/partials/slides/`
3. Flavor text system: `SLIDE_FLAVOR_TEXT` dict with random selection per slide type
4. Slides: intro, total_trophies, platinums, rarest_trophy, most_active_day, activity_calendar, games, badges, comparison, summary, 4 quiz types, streak, time_analysis

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
