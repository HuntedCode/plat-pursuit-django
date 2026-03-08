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

| Type | Template | Trigger |
|------|----------|---------|
| `monthly_recap` | `emails/monthly_recap.html` | Cron: `send_monthly_recap_emails` |
| `subscription_welcome` | `emails/subscription_welcome.html` | `activate_subscription()` (first time) |
| `payment_succeeded` | `emails/payment_succeeded.html` | Stripe/PayPal renewal webhook |
| `payment_failed` | `emails/payment_failed.html` | Stripe `invoice.payment_failed` webhook |
| `payment_failed_final` | `emails/payment_failed_final.html` | Final retry failure |
| `payment_action_required` | `emails/payment_action_required.html` | 3D Secure or action needed |
| `subscription_cancelled` | `emails/subscription_cancelled.html` | Cancellation confirmation |
| `donation_receipt` | `emails/donation_receipt.html` | Donation completion |
| `badge_claim_confirmation` | `emails/badge_claim_confirmation.html` | Fundraiser badge claim |
| `artwork_complete` | `emails/artwork_complete.html` | Admin marks artwork done |

### Email Template Pattern

All email templates extend `templates/emails/base_email.html` which provides:
- PlatPursuit branding and logo
- Responsive table-based layout (email client compatible)
- Footer with unsubscribe link
- Consistent gradient styling

### Testing Emails

```bash
# Preview recap email
python manage.py test_email_system user@example.com --recap-preview

# Preview subscription emails
python manage.py test_email_system user@example.com --welcome-preview
python manage.py test_email_system user@example.com --payment-succeeded-preview
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

## Related Docs

- [Monthly Recap](../features/monthly-recap.md): Recap email generation and sending
- [Subscription Lifecycle](../features/subscription-lifecycle.md): Payment lifecycle emails
- [Fundraiser](../features/fundraiser.md): Donation receipt and claim emails
- [Cron Jobs](cron-jobs.md): Email sending schedules
- [Management Commands](management-commands.md): Email testing commands
