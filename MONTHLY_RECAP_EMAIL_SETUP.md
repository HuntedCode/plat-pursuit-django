# Monthly Recap Email System - Setup Guide

This document explains the monthly recap email notification system and how to use it.

## Overview

The monthly recap email system automatically sends beautiful HTML emails to users when their monthly recap is available. Emails include:
- Non-spoiler teaser stats (total trophies, platinums, completions)
- Highlights of what's inside the full recap
- Call-to-action button linking to the interactive recap

## Architecture

### Components

1. **MonthlyRecap Model** ([trophies/models.py](trophies/models.py))
   - Added `email_sent` and `email_sent_at` fields to track email delivery
   - Prevents duplicate emails from being sent

2. **EmailService** ([core/services/email_service.py](core/services/email_service.py))
   - Reusable service for sending HTML emails via SendGrid
   - Supports single emails and bulk sending
   - Auto-generates plain-text fallback from HTML

3. **HTML Email Template** ([templates/emails/monthly_recap.html](templates/emails/monthly_recap.html))
   - Beautiful gradient design with responsive layout
   - Shows teaser stats without spoiling the full recap
   - Mobile-friendly and email client compatible

4. **Management Command** ([core/management/commands/send_monthly_recap_emails.py](core/management/commands/send_monthly_recap_emails.py))
   - Sends emails to users with finalized recaps
   - Dry-run mode for testing
   - Batch processing for scalability

## Setup Instructions

### 1. Run Database Migration

```bash
# Activate your virtual environment first
python manage.py makemigrations trophies
python manage.py migrate trophies
```

This adds `email_sent` and `email_sent_at` fields to the MonthlyRecap model.

### 2. Verify Email Configuration

Ensure your `settings.py` has email configured (already done):

```python
DEFAULT_FROM_EMAIL = 'no-reply@platpursuit.com'
EMAIL_BACKEND = 'sendgrid_backend.SendgridBackend'  # In production
```

### 3. Test the System (Dry Run)

Test without sending actual emails:

```bash
python manage.py send_monthly_recap_emails --dry-run
```

This shows what emails would be sent without actually sending them.

### 4. Test with a Single User

Send a test email to a specific profile:

```bash
# Find a profile ID first
python manage.py shell
>>> from trophies.models import MonthlyRecap
>>> recap = MonthlyRecap.objects.filter(is_finalized=True, profile__user__isnull=False).first()
>>> print(f"Profile ID: {recap.profile_id}, User: {recap.profile.psn_username}")

# Send email to that profile
python manage.py send_monthly_recap_emails --profile-id 123
```

### 5. Verify Email Delivery

Check that:
- Email was received (check inbox or SendGrid dashboard)
- HTML formatting looks good
- Links work correctly
- Plain text fallback is readable

### 6. Set Up Render Cron Job

Add this cron job to your Render dashboard:

```yaml
# Send monthly recap emails
# Runs on the 3rd of each month at 6:00 AM UTC (after recaps are generated)
0 6 3 * * python manage.py send_monthly_recap_emails
```

**Recommended Schedule:**
- **2nd of month, 11:00 PM UTC**: Generate and finalize recaps
  ```bash
  python manage.py generate_monthly_recaps --finalize
  ```
- **3rd of month, 6:00 AM UTC**: Send emails
  ```bash
  python manage.py send_monthly_recap_emails
  ```

This gives 7 hours for recap generation to complete before emails start sending.

## Usage

### Command Options

```bash
# Send all pending emails (default behavior)
python manage.py send_monthly_recap_emails

# Dry run - preview what would be sent
python manage.py send_monthly_recap_emails --dry-run

# Send for specific month only
python manage.py send_monthly_recap_emails --year 2026 --month 1

# Send to specific user
python manage.py send_monthly_recap_emails --profile-id 123

# Resend emails (even if already sent) - useful for testing
python manage.py send_monthly_recap_emails --force

# Custom batch size (default: 100)
python manage.py send_monthly_recap_emails --batch-size 50
```

### Email Recipient Criteria

Emails are sent to users who meet ALL these conditions:
1. ✅ Has a finalized recap (`is_finalized=True`)
2. ✅ Has a linked PSN account (`is_linked=True`)
3. ✅ Has a user account (`user__isnull=False`)
4. ✅ Has an email address (`user.email` exists)
5. ✅ Email not already sent (`email_sent=False`) unless `--force` is used

### Integration with Existing Commands

The recap email system integrates with the existing `generate_monthly_recaps` command:

```bash
# Option 1: Generate recaps, then send emails separately (recommended)
python manage.py generate_monthly_recaps --finalize  # Day 2-3 of month
python manage.py send_monthly_recap_emails           # Day 3 of month (later)

# Option 2: Generate recaps with in-app notifications (no email)
python manage.py generate_monthly_recaps --finalize --notify
# Note: --notify creates in-app notifications, not emails
```

## Email Template Customization

The email template is located at [templates/emails/monthly_recap.html](templates/emails/monthly_recap.html).

### Available Context Variables

- `username` - PSN username (display version)
- `month_name` - Full month name (e.g., "January")
- `year` - Year (e.g., 2026)
- `total_trophies` - Total trophies earned that month
- `platinums_earned` - Number of platinums earned
- `games_completed` - Number of 100% completions
- `badges_earned` - Number of badges earned
- `has_streak` - Boolean, true if user had a streak > 1 day
- `recap_url` - Full URL to the recap page
- `site_url` - Base site URL

### Design Principles

The email template follows these principles:
1. **Non-spoiler**: Shows just enough to entice, not spoil the full experience
2. **Mobile-first**: Responsive design works on all devices
3. **Clear CTA**: Prominent button to view full recap
4. **Brand consistency**: Uses PlatPursuit colors and style
5. **Accessible**: Plain text fallback for text-only email clients

## EmailService Reusability

The `EmailService` can be reused for other email needs:

```python
from core.services.email_service import EmailService

# Send a single HTML email
EmailService.send_html_email(
    subject="Welcome to PlatPursuit!",
    to_emails=['user@example.com'],
    template_name='emails/welcome.html',
    context={'username': 'John', 'signup_date': '2026-01-15'},
)

# Send bulk personalized emails
recipients = [
    {'email': 'user1@example.com', 'name': 'John'},
    {'email': 'user2@example.com', 'name': 'Jane'},
]

def get_context(recipient):
    return {'username': recipient['name']}

EmailService.send_bulk_html_email(
    subject="Your weekly digest",
    recipients=recipients,
    template_name='emails/digest.html',
    context_fn=get_context,
)
```

## Monitoring and Troubleshooting

### Check Email Status

```python
# In Django shell
from trophies.models import MonthlyRecap

# Find recaps with emails sent
sent = MonthlyRecap.objects.filter(email_sent=True).count()
print(f"{sent} emails sent")

# Find pending emails
pending = MonthlyRecap.objects.filter(
    is_finalized=True,
    email_sent=False,
    profile__is_linked=True,
    profile__user__isnull=False
).count()
print(f"{pending} emails pending")
```

### Check SendGrid Dashboard

1. Log into SendGrid
2. Navigate to "Activity"
3. Filter by date and subject line
4. Check delivery rates, opens, clicks, bounces

### Common Issues

**No emails being sent:**
- Check that recaps are finalized: `MonthlyRecap.objects.filter(is_finalized=True).count()`
- Verify email configuration in settings.py
- Check SendGrid API key is valid
- Run with `--dry-run` to see what would be sent

**Emails going to spam:**
- Verify SPF/DKIM records in SendGrid
- Check "From" email is verified in SendGrid
- Review email content for spam triggers

**Wrong data in emails:**
- Check MonthlyRecap model has correct data
- Verify template context in command
- Test with `--dry-run` first

## Future Enhancements

Potential improvements to consider:

1. **Email Preferences**: Add user setting to opt-out of recap emails
2. **A/B Testing**: Test different subject lines and content
3. **Personalization**: More personalized content based on user behavior
4. **Analytics**: Track email opens, clicks, recap views from email
5. **Digest Emails**: Weekly/monthly digest of other activity
6. **Unsubscribe Link**: One-click unsubscribe from recap emails

## Related Files

- [trophies/models.py](trophies/models.py) - MonthlyRecap model
- [core/services/email_service.py](core/services/email_service.py) - Email service
- [templates/emails/monthly_recap.html](templates/emails/monthly_recap.html) - Email template
- [core/management/commands/send_monthly_recap_emails.py](core/management/commands/send_monthly_recap_emails.py) - Management command
- [core/management/commands/generate_monthly_recaps.py](core/management/commands/generate_monthly_recaps.py) - Recap generation
- [notifications/models.py](notifications/models.py) - Notification types (includes 'monthly_recap')

## Support

For issues or questions:
- Check logs: `python manage.py send_monthly_recap_emails --dry-run`
- Review SendGrid dashboard for delivery issues
- Test with a single user first: `--profile-id <id>`
