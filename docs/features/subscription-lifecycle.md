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
| `premium_tier` | CharField (nullable) | Internal tier SLUG: one of the six ladder levels (backer ... cornerstone), or one of the three legacy slugs (premium_monthly, premium_yearly, supporter) until their holders are migrated. See Legacy Tier Migration below |
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

### The Membership Page's State Read

`/support/membership/` renders from `SubscriptionService.membership_status(user)` (read-only,
richer than the boolean `has_active_subscription`, which deliberately reports `(False, None)`
during grace (both providers) so the double-subscribe guard lets a cancelled member re-subscribe):

| State | Meaning | Source |
|---|---|---|
| `active` | live sub; `cancels_at` set when a cancel is scheduled (`cancel_at_period_end` OR a bare `cancel_at`) | Stripe active/trialing sub, or PayPal with no `paypal_cancel_at` |
| `past_due` | payment retrying, premium kept | Stripe `past_due` sub |
| `grace` | cancelled with paid time left (`grace_until`) | Stripe `canceled` + future `current_period_end`, or future `paypal_cancel_at` |
| `none` | no membership | everything else |

Ordering guard: an ACTIVE Stripe sub outranks PayPal (a real re-subscribe), but for a
`provider='paypal'` user STALE Stripe rows (past_due/canceled) never claim the page.

Companions: `premium_tenure(user)` (member-since / total months from `SubscriptionPeriod`; the
milestones `premium_months` metric delegates to it) and `describe_billing(user, membership)`
(amount + cycle for display -- Stripe from the djstripe plan, PayPal ladder via the cached
snapshot's plan id, PayPal legacy cycle-only; unknown values are omitted, never guessed).

The Stripe billing portal is a POST action (`stripe_billing_portal`) -- sessions are minted on
click, never on page GET. PayPal status/next-billing comes from a cached snapshot
(`paypal:sub:{mode}:{id}`, 8h TTL, 60s failure marker, busted by every PayPal subscription
webhook and the user's own cancel) so the page never hangs on a live PayPal call.

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
3. Checks PayPal: verifies stored subscription ID, tier, and that `paypal_cancel_at` is UNSET (a cancelled sub will not renew, so it does not block re-subscribing; expiry is handled by webhooks)
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

## Legacy Tier Migration (2026-09)

The three tiers that predate the supporter ladder (`premium_monthly` $3.99/mo, `premium_yearly`
$39.99/yr, `supporter` $20/mo) were withdrawn from sale in 2026-08 but kept renewing for existing
holders. `python manage.py migrate_legacy_tiers` moves them onto the ladder so the legacy layer can
be deleted rather than maintained forever. **Whether it has run against production is tracked in the
[deploy checklist](../design/rebuild/prod-deploy-checklist.md), not here.** Targets are by price
proximity, read from
`LEGACY_TIER_LEVEL_MAP`: both premium tiers become **Backer** ($4/$40, a one-cent increase),
`supporter` becomes **Sponsor** (an exact $20 match).

**The two arms are different, and one asymmetry is permanent.**

| | Stripe | PayPal |
|---|---|---|
| Mechanism | Real price swap (`Subscription.modify`, `proration_behavior='none'`, billing anchor untouched) | Adoption: no processor call at all |
| What the member pays after | The real ladder price | Their original legacy price, forever |
| Legacy processor objects | Archivable once verified | **Must stay live** |

PayPal's revise endpoint requires the *subscriber* to log in and re-approve whenever the billing
amount changes, so a one-cent increase is not something the site can apply on their behalf, and
cancel-and-resubscribe would fire the farewell email, pull the Discord role and risk losing a paying
member outright. So `LEGACY_PLAN_ADOPTION` (in `users/constants.py`) makes the two legacy PayPal
plan ids resolve to `backer` instead, and those members keep their price as a grandfathered
discount.

**Why the migration is silent.** NEITHER arm calls `activate_subscription` and neither re-derives
the tier from the djstripe mirror. Both write `premium_tier` directly and then call
`reconcile_premium`, the one premium truth-writer, so nothing announces and no Discord role is
re-pushed on either arm. Each arm has its own reason for avoiding the usual entry points: the Stripe
arm would otherwise write a payload at the SDK's pinned API version over a mirror whose other rows
came from webhooks at the account's version, and would expose itself to `update_user_subscription`'s
four fall-throughs to `deactivate_subscription`, for no gain when the target slug is already known;
the PayPal arm cannot use `activate_subscription` because it clears `paypal_cancel_at` and would
resurrect a member who is mid-cancellation.

`reconcile_premium` leaves an open `SubscriptionPeriod` alone while premium stays true, so tenure and
the `premium_months` milestone survive intact. `Profile.display_mark` does not change for a single
user either, because `worn_supporter_level` already collapsed `premium_monthly` onto `backer`: the
supporter wall, the leaderboards and every name on the site render identically before and after.

**What members do see.** The level NAME changes from "Premium Yearly" to "Backer" on the membership
page, the settings page and the welcome page, and in renewal and cancellation emails (all of which
render `get_tier_display_name(premium_tier)`). They also lose the "a founding tier" recognition line,
since `worn_level_dict`'s `is_legacy` branch stops matching. Adopted PayPal members are the sharp
case: they read as ordinary Backers while still being charged $3.99 against Backer's advertised $4.

**Discord roles are knowingly left inconsistent for `supporter` holders.** `sponsor` grants the
Premium role, and nothing removes the Premium+ role their legacy tier granted, because removal keys
off `original_tier` in `deactivate_subscription` (now `sponsor`). They keep both roles while
subscribed, and keep Premium+ after they churn. Accepted rather than fixed; see the deploy checklist.

## Email Preference System

### Default Preferences

All preferences default to `True` for new users:

| Key | Controls |
|-----|----------|
| `monthly_recap` | Monthly recap emails |
| `badge_notifications` | Badge-related emails |
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
- **Stripe grace period for canceled subscriptions**: A subscription with status `canceled` may still have paid time remaining. The system checks this before deactivating. **Always read that end date through `SubscriptionService.subscription_period_end`, never `stripe_data['current_period_end']` directly.** Stripe moved the field onto the subscription's ITEMS in the 2025-03-31 API version, so the top-level key is `None` on every payload written since; the five call sites that read it directly failed closed, and three of them revoked premium from members who had paid for time they had not used. The helper reads item-first with the top-level as fallback, so rows written at either API version resolve.
- **`django.utils.timezone.utc` does not exist**: it was removed in Django 5.0. Use `from datetime import timezone as dt_timezone` and `dt_timezone.utc`. This has bitten this subsystem four separate times, in three files, and each time it was invisible because `AttributeError` fell outside the surrounding `except` clause: two grace checks raised on every run, the audit command's copy sat unreachable behind the bug above, and `_send_payment_succeeded_email` raised on every renewal, which silently stopped renewal receipts sending entirely.
- **14-day recovery window**: When reopening a closed `SubscriptionPeriod`, only periods closed within the last 14 days are eligible. This covers Stripe's retry window. Older periods get a fresh start to keep milestone calculations accurate.
- **on_commit for Discord calls**: Discord role assignment/removal uses `transaction.on_commit()` to avoid blocking the webhook response with HTTP calls to the Discord bot. If the transaction rolls back, the role change is never sent.
- **Email suppression logging**: When an email is suppressed due to user preferences, `EmailService.log_suppressed()` creates an EmailLog entry with status "suppressed". This is critical for the admin dashboard to distinguish "user opted out" from "email failed to send".
- **Token expiration**: Email preference tokens expire after 90 days. After that, users must log in to manage preferences directly. The token is stateless (no database storage), so there is no way to invalidate a specific token early.

## Related Docs

- [Review Hub](review-hub.md): Premium features may gate certain review hub capabilities
- [Dashboard](dashboard.md): Dashboard modules may be premium-only
