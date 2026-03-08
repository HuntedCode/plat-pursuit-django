# Subscription Lifecycle

The subscription system manages the full lifecycle of paid subscriptions across two payment providers (Stripe and PayPal). It handles activation, deactivation, payment failures, 3D Secure authentication, grace periods, tier mapping, Discord role assignments, lifecycle emails, and an admin dashboard for monitoring subscriber health. The system is provider-agnostic at its core: `activate_subscription()` and `deactivate_subscription()` are called by both Stripe and PayPal webhook handlers, with provider-specific logic encapsulated in separate methods.

## Architecture Overview

The system is built around a provider-agnostic core with provider-specific adapters. `SubscriptionService.activate_subscription()` and `deactivate_subscription()` are the two central methods that all webhook handlers converge on. These methods update the user's `premium_tier` and `subscription_provider` fields, manage `SubscriptionPeriod` records for loyalty milestone tracking, handle Discord role assignments via `on_commit` callbacks, send lifecycle emails, and trigger milestone checks.

Stripe integration uses `djstripe` for syncing Stripe objects to the local database. The Stripe webhook handler (`handle_webhook_event`) routes seven event types: checkout completed, subscription created/updated/deleted, invoice paid, invoice payment failed, and invoice payment action required. Each event resolves the user via `stripe_customer_id`, then delegates to the appropriate handler method.

PayPal integration uses direct REST API calls. PayPal webhooks are handled in a separate `paypal_service.py` (not covered here) but converge on the same `activate_subscription()` / `deactivate_subscription()` methods. PayPal-specific state (subscription ID, cancel-at date) lives on the `CustomUser` model.

A double-subscribe guard (`has_active_subscription()`) prevents users from subscribing through both providers simultaneously. It checks Stripe via `djstripe` Subscription objects and PayPal via stored user fields.

Email delivery respects user preferences. Every lifecycle email checks `EmailPreferenceService.should_send_email()` before sending. If the user has opted out of `subscription_notifications` or enabled `global_unsubscribe`, the email is suppressed and logged via `EmailService.log_suppressed()` for audit visibility.

The admin dashboard at `/staff/subscriptions/` provides subscriber stats, an attention-needed queue (past-due, payment failures), a full subscriber list, and recent activity. Staff can resend emails, resend notifications, force-deactivate subscriptions, and view a user's full notification and email history through a detail modal.

## File Map

| File | Purpose |
|------|---------|
| `users/services/subscription_service.py` | Core service: activation, deactivation, tier mapping, checkout, payment failure handling, all lifecycle emails, webhook routing |
| `users/services/email_preference_service.py` | Email preference management: token generation/validation, preference CRUD, send-gate checks |
| `users/models.py` (CustomUser) | Payment fields: `stripe_customer_id`, `paypal_subscription_id`, `subscription_provider`, `paypal_cancel_at`, `premium_tier`, `email_preferences` |
| `users/models.py` (SubscriptionPeriod) | Loyalty tracking: open/close periods for milestone calculations |
| `core/models.py` (EmailLog) | Audit trail for all platform emails (sent, suppressed, admin-triggered) |
| `core/services/email_service.py` | SendGrid email delivery with built-in logging |
| `users/constants.py` | Tier definitions: STRIPE_PRODUCTS, STRIPE_PRICES, PREMIUM_TIER_DISPLAY, ACTIVE_PREMIUM_TIERS, Discord role tier mappings |
| `api/subscription_admin_views.py` | Staff-only API: resend emails, resend notifications, force deactivate, user detail modal |
| `trophies/views/admin_views.py` (related) | Token monitoring and other admin views (subscription dashboard is in a separate view file) |
| `users/views.py` | Stripe and PayPal webhook entry points (route to SubscriptionService) |
| `templates/emails/subscription_welcome.html` | Welcome email template |
| `templates/emails/payment_succeeded.html` | Renewal confirmation email template |
| `templates/emails/payment_failed.html` | Payment failure warning email template |
| `templates/emails/payment_action_required.html` | 3D Secure authentication email template |
| `templates/emails/subscription_cancelled.html` | Farewell email template |

## Data Model

### CustomUser (subscription-relevant fields)

| Field | Type | Notes |
|-------|------|-------|
| `stripe_customer_id` | CharField (nullable) | Stripe Customer ID, set on first checkout |
| `paypal_subscription_id` | CharField (nullable) | PayPal Subscription ID, set by webhook |
| `subscription_provider` | CharField (nullable) | "stripe" or "paypal" |
| `paypal_cancel_at` | DateTimeField (nullable) | When PayPal sub expires after cancellation |
| `premium_tier` | CharField (nullable) | Internal tier name: ad_free, premium_monthly, etc. |
| `email_preferences` | JSONField | Dict of preference key to boolean |

### SubscriptionPeriod

| Field | Type | Notes |
|-------|------|-------|
| `user` | FK to CustomUser | CASCADE |
| `started_at` | DateTimeField | When this period began |
| `ended_at` | DateTimeField (nullable) | NULL means currently active |
| `provider` | CharField | "stripe" or "paypal" |
| `notes` | CharField (blank) | Admin notes (e.g., "backfilled from launch") |

A DB partial unique constraint prevents duplicate open periods. Total subscription time = sum of all period durations (closed periods use `ended_at - started_at`).

### EmailLog

| Field | Type | Notes |
|-------|------|-------|
| `user` | FK to CustomUser (nullable) | The recipient user |
| `recipient_email` | EmailField | Recipient address |
| `email_type` | CharField | payment_failed, subscription_welcome, payment_succeeded, payment_action_required, subscription_cancelled, and more |
| `subject` | CharField | Email subject line |
| `status` | CharField | sent, failed, suppressed |
| `triggered_by` | CharField | webhook, admin_manual, cron, etc. |
| `metadata` | JSONField | Extra context (attempt_count, admin notes, etc.) |
| `created_at` | DateTimeField | Auto |

## Key Flows

### New Subscription Activation

1. User completes Stripe checkout or PayPal flow
2. Webhook fires (Stripe `customer.subscription.created` or PayPal `BILLING.SUBSCRIPTION.ACTIVATED`)
3. `SubscriptionService.activate_subscription(user, tier, provider, event_type)` called
4. Sets `premium_tier` and `subscription_provider` on user
5. Updates `profile.update_profile_premium(True)` in a transaction
6. Opens a `SubscriptionPeriod` (or reopens a recently closed one if within 14-day recovery window)
7. Sends Discord subscription notification (new subscriptions only)
8. Assigns Discord premium role via `on_commit` callback
9. Sends welcome email (new subscriptions only, respects email preferences)
10. Checks `is_premium` and `subscription_months` milestones

### Payment Failure Handling

1. Stripe `invoice.payment_failed` webhook fires
2. `handle_payment_failed(user, invoice_data)` extracts `attempt_count` and `next_payment_attempt`
3. In-app notification sent on every attempt (includes next retry timestamp for dashboard)
4. Email sent only on first failure and final warning (when `next_payment_attempt` is None)
5. For `past_due` status: premium features stay active, but `SubscriptionPeriod` is closed to stop milestone time accumulation
6. If Stripe exhausts retries (`unpaid` status): full deactivation via `deactivate_subscription()`

### 3D Secure / Payment Action Required

1. Stripe `invoice.payment_action_required` webhook fires
2. `handle_payment_action_required(user, invoice_data)` extracts `hosted_invoice_url`
3. Sends in-app notification and email with link to Stripe's hosted invoice for authentication
4. No subscription status change: premium stays active while user completes verification

### Subscription Cancellation

1. Stripe `customer.subscription.deleted` or PayPal `BILLING.SUBSCRIPTION.EXPIRED` webhook fires
2. `deactivate_subscription(user, provider, event_type)` called
3. Clears `premium_tier` and `subscription_provider`
4. Updates `profile.update_profile_premium(False)` in a transaction
5. Closes any open `SubscriptionPeriod`
6. Removes Discord premium role via `on_commit` callback
7. Sends farewell email and creates in-app notification with resubscribe link

### Successful Renewal Payment

1. Stripe `invoice.paid` webhook fires
2. `update_user_subscription()` called first to refresh subscription state
3. `handle_payment_succeeded(user, invoice_data)` called next
4. Skips initial subscription invoices (`billing_reason == 'subscription_create'`) since welcome email handles those
5. Skips $0 invoices (prorations, trials)
6. Sends payment confirmation email with next billing date

### PayPal Cancellation (Pending Expiry)

1. PayPal `BILLING.SUBSCRIPTION.CANCELLED` fires
2. `mark_subscription_cancelling(user, cancel_at)` stores the expiry date
3. Premium remains active until `paypal_cancel_at` passes
4. PayPal `BILLING.SUBSCRIPTION.EXPIRED` fires at expiry, triggering full `deactivate_subscription()`

### Double-Subscribe Guard

1. Before creating a checkout session or PayPal subscription, `has_active_subscription(user)` is called
2. Checks Stripe: queries `djstripe.Subscription` for active or past_due status
3. Checks PayPal: verifies stored subscription ID, tier, and that `paypal_cancel_at` has not passed
4. Returns `(True, provider_name)` if active sub exists, blocking the new subscription attempt

## API Endpoints

### User-Facing

Checkout and subscription management endpoints are in `users/views.py` (not API views). They use standard Django views with redirects to Stripe/PayPal.

### Admin Dashboard

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/admin/subscriptions/action/` | Staff | Actions: resend_payment_email, resend_payment_email_final, resend_notification, force_deactivate, send_welcome_email, send_payment_succeeded_email, resend_action_required_email |
| GET | `/api/v1/admin/subscriptions/user/<user_id>/` | Staff | User detail: notification history, email logs, subscription periods |

### Admin Action Payloads

All actions POST to the same endpoint with `action` and `user_id` in the request body:

| Action | Additional Fields | Effect |
|--------|-------------------|--------|
| `resend_payment_email` | None | Sends first-warning payment failed email |
| `resend_payment_email_final` | None | Sends final-warning payment failed email |
| `resend_notification` | `attempt_count`, `is_final` | Creates in-app payment failed notification |
| `force_deactivate` | `notes` (optional) | Deactivates subscription, logs to EmailLog |
| `send_welcome_email` | None | Re-sends welcome email |
| `send_payment_succeeded_email` | None | Re-sends payment confirmation email |
| `resend_action_required_email` | None | Re-sends 3D Secure email (finds invoice URL from latest notification) |

## Email Preference System

### Default Preferences

All preferences default to `True` for new users:

| Key | Controls |
|-----|----------|
| `monthly_recap` | Monthly recap emails |
| `badge_notifications` | Badge-related emails |
| `milestone_notifications` | Milestone achievement emails |
| `subscription_notifications` | All subscription lifecycle emails |
| `admin_announcements` | Staff announcements |
| `global_unsubscribe` | Master kill switch (defaults to False) |

### Token-Based Preference Access

Email preference links use stateless signed tokens (Django's `TimestampSigner`), valid for 90 days. Users can manage preferences without logging in by clicking the link in any email footer. Token format: `{user_id}:{timestamp}:{signature}`.

### Global Unsubscribe Behavior

When `global_unsubscribe` is set to `True`, all other preferences are forced to `False`. This is enforced at the update level in `update_user_preferences()`.

## Integration Points

- **Discord roles**: Premium and Supporter tier roles are assigned/removed via `notify_bot_role_earned()` / `notify_bot_role_removed()`, deferred with `transaction.on_commit()` to avoid blocking webhook responses.
- **Milestone system**: `is_premium` and `subscription_months` milestone criteria are checked on every activation. Subscription months are calculated from accumulated `SubscriptionPeriod` durations.
- **SubscriptionPeriod and past_due**: When a subscription goes `past_due`, the period is closed to stop time accumulation. On payment recovery (past_due to active), the most recently closed period (within 14 days) is reopened rather than creating a new one.
- **EmailService logging**: Every email (sent or suppressed) is recorded in `EmailLog`. Admin-triggered actions also create EmailLog entries for audit trail.
- **Profile.update_profile_premium()**: Sets the profile-level premium flag used for feature gating across the platform.
- **Fundraiser webhooks**: Donation payments (Stripe `checkout.session.completed` with mode=payment, PayPal Orders API) are intercepted BEFORE subscription handlers in the webhook views.

## Gotchas and Pitfalls

- **PayPal double-email guard**: PayPal fires both `BILLING.SUBSCRIPTION.ACTIVATED` and `PAYMENT.SALE.COMPLETED` on initial subscription. The welcome email is sent in `activate_subscription()` (from the activation event), and `handle_payment_succeeded()` skips initial invoices by checking `billing_reason`. For PayPal, the payment succeeded handler also checks for a recent `subscription_welcome` EmailLog before sending.
- **past_due keeps premium active**: Unlike `unpaid` which triggers full deactivation, `past_due` preserves premium features. This is intentional: Stripe is still retrying payment, and revoking access during retry would cause a bad user experience. However, `SubscriptionPeriod` is closed to prevent milestone time from accumulating during the unpaid window.
- **Stripe grace period for canceled subscriptions**: A subscription with status `canceled` may still have paid time remaining (`current_period_end` in the future). The system checks this before deactivating.
- **14-day recovery window**: When reopening a closed `SubscriptionPeriod`, only periods closed within the last 14 days are eligible. This covers Stripe's retry window. Older periods get a fresh start to keep milestone calculations accurate.
- **on_commit for Discord calls**: Discord role assignment/removal uses `transaction.on_commit()` to avoid blocking the webhook response with HTTP calls to the Discord bot. If the transaction rolls back, the role change is never sent.
- **Email suppression logging**: When an email is suppressed due to user preferences, `EmailService.log_suppressed()` creates an EmailLog entry with status "suppressed". This is critical for the admin dashboard to distinguish "user opted out" from "email failed to send".
- **Token expiration**: Email preference tokens expire after 90 days. After that, users must log in to manage preferences directly. The token is stateless (no database storage), so there is no way to invalidate a specific token early.

## Related Docs

- [Community Hub](../community-hub.md): Premium features may gate certain community hub capabilities
- [Dashboard](../dashboard.md): Dashboard modules may be premium-only
