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
| `milestone_achieved` | `emails/milestone_achieved.html` | Sync: `send_consolidated_milestone_email()` in signals.py | `milestone_notifications` |
| `welcome` | `emails/welcome.html` | Verification: `VerificationService.link_profile_to_user()` | None (transactional) |
| `admin_announcement` | `emails/broadcast.html` | Admin: Notification Center broadcast | `admin_announcements` |
| `subscription_welcome` | `emails/subscription_welcome.html` | `activate_subscription()` (first time) | `subscription_notifications` |
| `payment_succeeded` | `emails/payment_succeeded.html` | Stripe/PayPal renewal webhook | `subscription_notifications` |
| `payment_failed` | `emails/payment_failed.html` | Stripe `invoice.payment_failed` webhook | `subscription_notifications` |
| `payment_failed_final` | `emails/payment_failed_final.html` | Final retry failure | `subscription_notifications` |
| `payment_action_required` | `emails/payment_action_required.html` | 3D Secure or action needed | `subscription_notifications` |
| `subscription_cancelled` | `emails/subscription_cancelled.html` | Cancellation confirmation | `subscription_notifications` |
| `donation_receipt` | `emails/donation_receipt.html` | Donation completion | None (transactional) |
| `badge_claim_confirmation` | `emails/badge_claim_confirmation.html` | Fundraiser badge claim | None (transactional) |
| `artwork_complete` | `emails/artwork_complete.html` | Admin marks artwork done | None (transactional) |

### Email Template Pattern

All email templates extend `templates/emails/base_email.html` which provides:
- PlatPursuit branding and logo
- Responsive table-based layout (email client compatible)
- Footer with unsubscribe link
- Consistent gradient styling

### Achievement Emails (Badge & Milestone)

Badge and milestone emails are sent automatically during the PSN sync cycle:

**Badge Earned Email** (`badge_earned`): Consolidates all badges earned in a single sync into one email. Triggered from `DeferredNotificationService._flush_profile_badges()` after in-app badge notifications are created. Lists each badge with series name, tier, progress bar, and next tier info.

**Milestone Achieved Email** (`milestone_achieved`): Consolidates all milestones earned in a single sync into one email. Triggered from `send_consolidated_milestone_email()` in `notifications/signals.py`, called by `token_keeper.py` after all milestone checks complete. For non-sync paths (reviews, ratings, etc.), `check_all_milestones_for_user()` sends the email immediately via the `send_email=True` default. Shows milestone name, description, title reward (if any), tier info, and next milestone progress. Handles both single and multiple milestones in one email.

Both are gated by their respective email preferences (`badge_notifications`, `milestone_notifications`). Suppressed sends are logged to EmailLog.

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

Users can manage email preferences via:
- Token-based links in email footers (works without login)
- Settings page: `/users/settings/` has an "Email Preferences" section that generates a token and redirects to the preference page

The `EmailPreferencesRedirectView` at `/users/email-preferences/redirect/` handles the logged-in redirect flow.

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
