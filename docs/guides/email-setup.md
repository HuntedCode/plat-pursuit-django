# Email Setup

PlatPursuit uses two email systems: **SendGrid** for transactional emails (receipts, notifications, recaps) and **Cloudflare Email Routing** for receiving PSN token verification emails. This guide covers both.

## SendGrid (Transactional Emails)

### Overview

All outbound emails go through SendGrid via `django-sendgrid-v5`. The `EmailService` class in `core/services/email_service.py` provides a consistent interface for all email types.

### Configuration

```env
SENDGRID_API_KEY=SG....
```

In `settings.py`:
```python
EMAIL_BACKEND = 'sendgrid_backend.SendgridBackend'
DEFAULT_FROM_EMAIL = 'no-reply@platpursuit.com'
```

In DEBUG mode, Django falls back to console email backend (emails print to terminal).

### EmailService API

```python
from core.services.email_service import EmailService

# Send a single HTML email
EmailService.send_html_email(
    subject="Your Monthly Recap",
    to_emails=['user@example.com'],
    template_name='emails/monthly_recap.html',
    context={'username': 'John', 'month': 'January'},
    log_email_type='monthly_recap',          # Creates EmailLog entry
    log_user=user,                           # Links to User model
    log_triggered_by='management_command',   # Origin tracking
)

# Send bulk personalized emails
EmailService.send_bulk_html_email(
    subject="Weekly Digest",
    recipients=[{'email': 'user@example.com', 'name': 'John'}],
    template_name='emails/digest.html',
    context_fn=lambda r: {'username': r['name']},
)
```

### EmailLog Audit Trail

Every email sent through `EmailService` (when `log_email_type` is provided) creates an `EmailLog` record in `core/models.py`:

| Field | Purpose |
|-------|---------|
| `email_type` | Category (monthly_recap, payment_failed, donation_receipt, etc.) |
| `user` | FK to User (nullable) |
| `recipient_email` | Actual email address |
| `subject` | Email subject line |
| `triggered_by` | Origin: system, webhook, admin_manual, management_command |
| `metadata` | JSONField for extra context (donation_id, series_slug, etc.) |
| `created_at` | Timestamp |

The `log_suppressed()` helper creates a log entry even when an email is not sent (e.g., user opted out), marking `was_suppressed=True`.

### EmailPreferenceService

Users can opt out of emails via token-based preference URLs. `EmailPreferenceService` checks preferences before sending and provides unsubscribe tokens for email templates.

### Email Types

| Type | Template | Trigger | Preference Gate |
|------|----------|---------|-----------------|
| `monthly_recap` | `emails/monthly_recap.html` | Cron: `send_monthly_recap_emails` | `monthly_recap` |
| `weekly_digest` | `emails/weekly_digest.html` | Cron: `send_weekly_digest` (Monday 08:00 UTC). Community-focused "This Week in PlatPursuit" newsletter. | `weekly_digest` |
| `badge_earned` | `emails/badge_earned.html` | Sync: `DeferredNotificationService._flush_profile_badges()` | `badge_notifications` |
| `welcome` | `emails/welcome.html` | Verification: `VerificationService.link_profile_to_user()` | None (transactional) |
| `launch_announcement` | `emails/launch_announcement.html` | Manual: `send_launch_announcement --send` (a few days post-cutover) | `global_unsubscribe`; also gated by `LAUNCH_ANNOUNCEMENT_SEND_ENABLED` |
| `admin_announcement` | `emails/broadcast.html` | Admin: Notification Center broadcast | `admin_announcements` |
| `subscription_welcome` | `emails/subscription_welcome.html` | `activate_subscription()` (first time) | `subscription_notifications` |
| `payment_succeeded` | `emails/payment_succeeded.html` | Stripe/PayPal renewal webhook | `subscription_notifications` |
| `payment_failed` | `emails/payment_failed.html` | Stripe `invoice.payment_failed` webhook | `subscription_notifications` |
| `payment_action_required` | `emails/payment_action_required.html` | 3D Secure or action needed | `subscription_notifications` |
| `subscription_cancelled` | `emails/subscription_cancelled.html` | Cancellation confirmation | `subscription_notifications` |
| `donation_receipt` | `emails/donation_receipt.html` | Donation completion | None (transactional) |
| `badge_claim_confirmation` | `emails/badge_claim_confirmation.html` | Fundraiser badge claim | None (transactional) |
| `artwork_complete` | `emails/artwork_complete.html` | Admin marks artwork done | None (transactional) |

### Email Template Pattern: two bases, mid-migration

Emails are moving onto a rebuilt base one template at a time. Both bases coexist until the
last child migrates, at which point the legacy base and this table row die together.

| Base | Children | Notes |
|------|----------|-------|
| `base_email_v2.html` | `welcome.html`, `launch_announcement.html`, `email_verification.html`, `password_reset.html` | The target. Extend this for anything new or rebuilt. |
| `base_email.html` (legacy) | the remaining kept templates (`subscription_welcome`, `payment_succeeded`, `payment_failed`, `payment_action_required`, `subscription_cancelled`, `donation_receipt`, `badge_claim_confirmation`, `artwork_complete`, `badge_earned`) + the parked recap/digest/broadcast | Div-based, no MSO handling, no preheader, `#667eea` purple that exists nowhere in the site's brand. Retired child-by-child. |

**What v2 provides:**
- A `role="presentation"` table scaffold with MSO ghost tables, so Outlook renders it.
- A **bulletproof CTA** via `{% include 'emails/_cta_button.html' with url=... label=... %}` (a VML
  roundrect for Outlook plus an anchor for everyone else). A styled `<a>` alone collapses in Outlook.
- A **`preheader` block**: the inbox preview line. Write a real sentence (see the plaintext rule below).
- **Light content body, dark brand bands.** Settled deliberately: Gmail and Outlook force their own
  transforms in dark mode and are worst on committed-dark email (near-black body with near-white text
  is what partial inversion mangles into grey-on-grey). A light body transforms predictably; the brand
  lands in the bands, which survive inversion far better. `color-scheme` is declared light, with a
  `prefers-color-scheme` tune for Apple Mail.
- Brand hex converted from the site's `oklch` tokens, which no email client can parse.
- The system font stack. The site's self-hosted Bricolage and Inter never reach an inbox.

**Blocks:** `title`, `preheader`, `extra_styles`, `header_content`, `content`, `footer_note`.
A child migrates by changing its `{% extends %}` line and adding a preheader.

### The plaintext rule (load-bearing)

There are no `.txt` templates. The `text/plain` part of every email is the HTML with
`<style>`/`<script>` elements dropped and then `strip_tags` applied, which **discards every href**.
(Dropping those elements first matters: `strip_tags` removes tags, not their contents, so
stripping straight from the source used to dump the entire stylesheet into the plaintext part
before the first sentence.) So any URL the reader must be able to reach has to appear as **visible
text**, not only as a link target. `emails/welcome.html` shows the pattern (a CTA button followed by
"Or paste this into your browser: ..."), and `tests/engine/test_auth_pages.py` pins the same rule for
the verification link.

### Badge Earned Email

Badge emails are sent automatically during the PSN sync cycle. The `badge_earned` email consolidates all badges earned in a single sync into one email. Triggered from `DeferredNotificationService._flush_profile_badges()` after in-app badge notifications are created. Lists each badge with series name, tier, progress bar, and next tier info. Gated by the `badge_notifications` email preference. Suppressed sends are logged to EmailLog.

Milestone achievements generate in-app notifications only (no email).

### Welcome Email

Sent once after a user verifies their PSN account. Triggered from `VerificationService.link_profile_to_user()` in `trophies/services/verification_service.py`. Idempotent: checks `EmailLog.objects.filter(user=user, email_type='welcome').exists()` before sending. No preference gate (one-time transactional email).

### Broadcast Center (Admin Email)

The Notification Center at `/staff/notifications/` supports sending companion emails alongside in-app notifications. The email automatically mirrors the in-app notification content: no separate email body is needed.

1. Compose notification as normal (title, message, sections, audience, etc.)
2. Toggle "Also send email"
3. Send immediately or schedule for later

The email renders the same title, message, structured sections (or legacy detail), banner image, and action button in a styled email layout matching the in-app announcement design.

Server-side rendering of structured sections and mini-markup (`*bold*`, `_italic_`, `` `code` ``, `[link](url)`, `- bullets`) is handled by `notifications/services/broadcast_email_renderer.py`.

Emails are gated by the `admin_announcements` preference. `NotificationLog` tracks `emails_sent` and `emails_suppressed` counts.

### Email Preferences Access

**PARKED (2026-08)** with the non-vital emails, pending the email-system rebuild. Only vital
emails send now (auth, billing, fundraiser, membership welcome) and all are transactional, so
there is nothing to opt out of. Both `/users/email-preferences/` routes 302 to Settings (old
tokened footer links land there); `EmailPreferencesView`, `EmailPreferencesRedirectView`, the
form and `EmailPreferenceService` are kept unrouted as the rebuild's starting point. Kept
templates' footers link Account Settings instead of the preference page.

### Testing Emails

```bash
# Preview recap email
python manage.py test_email_system user@example.com --recap-preview

# Preview subscription emails
python manage.py test_email_system user@example.com --welcome-preview
python manage.py test_email_system user@example.com --payment-succeeded-preview

# Preview new achievement emails
python manage.py test_email_system user@example.com --badge-earned-preview
python manage.py test_email_system user@example.com --milestone-preview

# Preview free user welcome email
python manage.py test_email_system user@example.com --free-welcome-preview

# Preview admin broadcast email
python manage.py test_email_system user@example.com --broadcast-preview

# Preview weekly digest email
python manage.py test_email_system user@example.com --weekly-digest-preview
```

## Cloudflare Email Routing (PSN Token Emails)

### Purpose

Generate unlimited unique email addresses (`tokenN@platpursuit.com`) for creating PSN accounts to obtain additional API tokens. More tokens = higher sync throughput by bypassing per-token rate limits.

### How It Works

Cloudflare Email Routing forwards all incoming mail for the domain to a single real inbox:

```
token1@platpursuit.com   --\
token2@platpursuit.com   ----> Cloudflare forwards ----> your-real@email.com
token347@platpursuit.com --/
```

### Setup

1. Ensure PlatPursuit domain DNS is managed by Cloudflare
2. Cloudflare Dashboard: **Email** > **Email Routing**
3. Add your real email address as the forwarding destination (verify it)
4. Enable the **Catch-all** rule: routes all `*@platpursuit.com` to your real email
5. Cloudflare auto-configures MX and SPF DNS records

### Key Points

- **Receive-only**: Cannot send FROM these addresses (not needed for PSN verification)
- **Free tier**: No ongoing cost or maintenance
- **No limit**: Create as many `tokenN@platpursuit.com` addresses as needed
- **Outbound email** (transactional) uses SendGrid, which is a separate system entirely

## DNS Records

For email to work correctly, the domain needs:
- **MX records**: Managed by Cloudflare (for incoming email routing)
- **SPF record**: Includes both Cloudflare and SendGrid
- **DKIM**: Configured in SendGrid for deliverability
- **DMARC**: Optional but recommended for spam prevention

## Gotchas and Pitfalls

- **DEBUG mode skips SendGrid**: Emails print to console instead. Check terminal output when testing locally.
- **EmailLog vs email sending**: `log_email_type` creates an audit record. The email still sends even without it, but you lose tracking.
- **Suppressed emails**: If a user opts out via `EmailPreferenceService`, use `log_suppressed()` to record that the email was intentionally not sent.
- **PayPal double-email guard**: For payment_succeeded emails, the system checks for a recent `subscription_welcome` EmailLog to prevent sending both welcome + payment emails on initial subscription.
- **SendGrid rate limits**: Bulk email commands use `--batch-size` (default 100) to avoid hitting SendGrid's API limits.
- **Broadcast emails iterate individually**: Each recipient gets a personalized email (with their name and preference token). This uses `iterator(chunk_size=200)` to avoid loading all users into memory at once.
- **Badge email consolidation**: One email per sync cycle, matching the in-app notification consolidation pattern. All badges earned in that sync are listed in a single email.
- **Welcome email idempotency**: Checked via EmailLog, not a user field. If the EmailLog record is deleted, the email could re-send on next verification. This is by design (safe to re-send a welcome).
- **Broadcast email mirroring**: The email automatically renders the same content as the in-app notification (title, message, sections, banner, CTA). No separate markdown body is needed. Legacy scheduled notifications with `email_body_markdown` populated use a fallback rendering path (`_send_broadcast_emails_legacy`).

## Related Docs

- [Monthly Recap](../features/monthly-recap.md): Recap email generation and sending
- [Subscription Lifecycle](../features/subscription-lifecycle.md): Payment lifecycle emails
- [Fundraiser](../features/fundraiser.md): Donation receipt and claim emails
- [Cron Jobs](cron-jobs.md): Email sending schedules
- [Management Commands](management-commands.md): Email testing commands
