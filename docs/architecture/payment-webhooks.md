# Payment & Webhooks

PlatPursuit accepts payments through two providers (Stripe and PayPal) for two distinct payment types: recurring subscriptions that unlock premium features, and one-time donations that fund badge artwork commissions. Both providers share a single webhook endpoint per provider, with routing logic that intercepts donation events before falling through to subscription handling. The system is built around a provider-agnostic core (`activate_subscription` / `deactivate_subscription`) that both Stripe and PayPal webhook handlers delegate to, keeping premium status management centralized.

## Architecture Overview

The payment architecture has four key design decisions:

1. **Two providers, one lifecycle.** Stripe and PayPal each have their own checkout and webhook flows, but both converge on `SubscriptionService.activate_subscription()` and `deactivate_subscription()` for state changes. This means premium tier assignment, Discord role management, email notifications, milestone checks, and `SubscriptionPeriod` tracking all live in exactly one place.

2. **Two payment types sharing webhook endpoints.** Rather than separate webhook URLs for subscriptions vs. donations, each provider has a single endpoint (`/stripe/webhook/` and `/paypal/webhook/`). Donation events are identified by metadata and intercepted first; everything else falls through to subscription logic.

3. **Double-subscribe guard.** A user can only have one active subscription across all providers. `has_active_subscription()` checks both Stripe (via djstripe `Subscription` records) and PayPal (via stored `paypal_subscription_id` + `premium_tier` on `CustomUser`) before allowing checkout.

4. **Test vs. live mode.** Stripe uses `settings.STRIPE_MODE` and PayPal uses `settings.PAYPAL_MODE` to select between sandbox/test and live product/price/plan IDs. All IDs are centralized in `users/constants.py` with separate dictionaries per mode.

## File Map

| File | Purpose |
|------|---------|
| `users/constants.py` | All Stripe product/price IDs, PayPal plan IDs, tier definitions, and reverse-lookup maps |
| `users/services/subscription_service.py` | Provider-agnostic subscription lifecycle, Stripe checkout creation, Stripe webhook event routing, payment failure/success email and notification logic |
| `users/services/paypal_service.py` | PayPal OAuth2 token management (Redis-cached), subscription creation, cancellation, webhook signature verification, PayPal-specific webhook event routing |
| `users/views.py` | `stripe_webhook()` and `paypal_webhook()` HTTP handlers, `subscribe()` checkout view, `subscribe_success()` return handler, `SubscriptionManagementView`, `paypal_cancel_subscription()` |
| `fundraiser/services/donation_service.py` | One-time payment checkout creation (Stripe `mode='payment'`, PayPal Orders API v2), donation completion, badge claiming, receipt/notification emails |
| `fundraiser/models.py` | `Fundraiser`, `Donation`, and `DonationBadgeClaim` models |
| `users/models.py` | `CustomUser` (payment fields), `SubscriptionPeriod` model |
| `core/models.py` | `EmailLog` model for email audit trail |
| `users/services/email_preference_service.py` | Email opt-out checks (consulted before every transactional email) |
| `core/services/email_service.py` | Shared `send_html_email()` with optional `EmailLog` recording |

## Data Model

### CustomUser (users/models.py)

Payment-related fields on the user model:

| Field | Type | Purpose |
|-------|------|---------|
| `stripe_customer_id` | CharField | Stripe Customer ID, set on first checkout |
| `paypal_subscription_id` | CharField | Active PayPal subscription ID, set by ACTIVATED webhook |
| `subscription_provider` | CharField | `'stripe'` or `'paypal'`, tracks which provider is active |
| `premium_tier` | CharField | Current tier: `ad_free`, `premium_monthly`, `premium_yearly`, or `supporter` |
| `paypal_cancel_at` | DateTimeField | When a cancelled PayPal subscription will expire (NULL = not cancelling) |

### SubscriptionPeriod (users/models.py)

Tracks continuous subscription windows for loyalty milestone calculations. A new period is created on activation; `ended_at` is set on deactivation. During payment recovery (past_due to active), the most recently closed period is reopened if it was closed within the last 14 days.

### Donation (fundraiser/models.py)

One-time payments tied to a `Fundraiser` campaign. Key fields: `provider` (stripe/paypal), `provider_transaction_id` (Stripe Session ID or PayPal Order ID, unique), `status` (pending/completed/failed/refunded), `badge_picks_earned` (calculated as `floor(amount / 10)` on completion), `badge_picks_used`.

### DonationBadgeClaim (fundraiser/models.py)

Links a `Donation` to a specific `Badge` (tier 1) via a `OneToOneField`, enforcing at the database level that each badge series can only be claimed by one donor. Tracks claim status through `claimed` -> `in_progress` -> `completed`.

### EmailLog (core/models.py)

General-purpose audit trail for all platform emails. Every email sent or suppressed (due to user preferences) is recorded with `email_type`, `user`, `subject`, `triggered_by`, and optional `metadata`. Used by the subscription admin dashboard to detect duplicate emails (e.g., the PayPal double-email guard).

## Key Flows

### Subscription Checkout Flow (Stripe)

1. User visits `/users/subscribe/` (the `subscribe()` view).
2. `has_active_subscription()` checks for existing active subscriptions across both providers. If active, user is redirected to subscription management.
3. User selects a tier and clicks subscribe. POST handler calls `SubscriptionService.create_checkout_session()`.
4. Checkout session is created with `mode='subscription'`, accepted payment methods (`card`, `us_bank_account`, `amazon_pay`, `cashapp`, `link`), and tier metadata.
5. If user has no Stripe customer, one is created via `Customer.get_or_create()` (djstripe). The `stripe_customer_id` is saved on the user.
6. User is redirected (303) to the Stripe-hosted checkout page.
7. On success, Stripe redirects to `/users/subscribe/success/?session_id={id}`. The view verifies payment status and shows a success message.
8. Stripe fires `checkout.session.completed` and `customer.subscription.created` webhooks. The webhook handler calls `update_user_subscription()`, which maps the product ID to a tier and delegates to `activate_subscription()`.

### Subscription Checkout Flow (PayPal)

1. Same subscribe page, but user selects PayPal as provider.
2. POST handler calls `PayPalService.create_subscription()`, which creates a subscription via `/v1/billing/subscriptions` with the user's `custom_id` set to their user ID.
3. User is redirected to PayPal's approval URL.
4. After approval, PayPal redirects to `/users/subscribe/success/?provider=paypal`. A success message is shown, but activation happens asynchronously via webhook.
5. PayPal fires `BILLING.SUBSCRIPTION.ACTIVATED`. The handler looks up the user by `custom_id` (since `paypal_subscription_id` is not yet stored), sets it on the user object, maps `plan_id` to tier, and delegates to `activate_subscription()`.

### Donation Checkout Flow (Stripe)

1. User visits the fundraiser page and fills in the donation form.
2. `DonationService.create_stripe_checkout()` creates a `Donation` record (status=`pending`) and a Stripe Checkout Session with `mode='payment'` (not subscription). Metadata includes `type: 'fundraiser_donation'` and the `donation_id`.
3. User completes payment on Stripe's hosted page.
4. Stripe fires `checkout.session.completed`. In `stripe_webhook()`, the routing logic checks metadata for `type == 'fundraiser_donation'` BEFORE passing to subscription handling.
5. `DonationService.handle_stripe_payment_completed()` looks up the pending Donation by ID and calls `complete_donation()`.
6. Completion: status set to `completed`, badge picks calculated, fundraiser milestone granted, receipt email sent, Discord notification posted.

### Donation Checkout Flow (PayPal)

1. `DonationService.create_paypal_order()` creates a `Donation` record and a PayPal Order via `/v2/checkout/orders` (Orders API v2, not the Subscriptions API).
2. User approves on PayPal, is redirected back.
3. On the success page, `capture_paypal_order()` is called immediately for fast UX (captures the authorized payment).
4. PayPal fires `PAYMENT.CAPTURE.COMPLETED` as a backup webhook. In `paypal_webhook()`, the handler calls `DonationService.handle_paypal_capture_completed()` BEFORE falling through to subscription logic.
5. The handler extracts `custom_id` (the donation ID) from the nested capture data, finds the pending Donation, and calls `complete_donation()`.

### Webhook Routing

This is the most critical architectural detail. Both webhook endpoints use a "donation-first" routing pattern:

**Stripe (`stripe_webhook()`):**
```
1. Verify signature using DJSTRIPE_WEBHOOK_SECRET
2. Process event through djstripe (DJStripeEvent.process)
3. IF checkout.session.completed AND metadata.type == 'fundraiser_donation':
     -> DonationService.handle_stripe_payment_completed()
     -> RETURN 200 (skip subscription handling entirely)
4. ELSE:
     -> SubscriptionService.handle_webhook_event() handles all of:
        - checkout.session.completed (subscription)
        - customer.subscription.created/updated/deleted
        - invoice.paid
        - invoice.payment_failed
        - invoice.payment_action_required
```

**PayPal (`paypal_webhook()`):**
```
1. Parse JSON body, verify signature via PayPal verification endpoint
2. Idempotency check: skip if transmission_id was seen in last 7 days (Redis)
3. IF CHECKOUT.ORDER.APPROVED:
     -> Log and RETURN 200 (capture happens on redirect, not webhook)
4. IF PAYMENT.CAPTURE.COMPLETED:
     -> TRY DonationService.handle_paypal_capture_completed()
     -> If it returns True (matched a donation): RETURN 200
     -> If False (not a donation): FALL THROUGH to subscription handler
5. PayPalService.handle_webhook_event() handles:
   - BILLING.SUBSCRIPTION.ACTIVATED
   - BILLING.SUBSCRIPTION.CANCELLED
   - BILLING.SUBSCRIPTION.SUSPENDED
   - BILLING.SUBSCRIPTION.EXPIRED
   - PAYMENT.SALE.COMPLETED (subscription renewal)
```

The key difference: Stripe donation checkout uses a completely distinct event (`checkout.session.completed` with metadata), so it can be intercepted cleanly. PayPal's `PAYMENT.CAPTURE.COMPLETED` can be either a donation capture or a subscription sale, so it tries the donation path first and falls through if no matching donation is found.

### Subscription Renewal

**Stripe:** The `invoice.paid` event fires on each successful renewal. The handler calls both `update_user_subscription()` (to refresh tier/status) and `handle_payment_succeeded()`. The payment succeeded email is only sent for renewals: `billing_reason == 'subscription_create'` and `amount_paid <= 0` invoices are both skipped.

**PayPal:** The `PAYMENT.SALE.COMPLETED` event fires on renewal. The handler sends a payment succeeded email, but checks for a recent `subscription_welcome` `EmailLog` entry (within 5 minutes) to avoid double-emailing on initial subscription. See Gotchas section for details.

### Subscription Cancellation

**Stripe:** User cancels via the Stripe billing portal (linked from the subscription management page). Stripe fires `customer.subscription.deleted`. `update_user_subscription()` detects no active subscription and calls `deactivate_subscription()`. Stripe may honor a grace period (cancel at period end), during which the canceled subscription's `current_period_end` is checked to retain premium.

**PayPal:** User cancels via the in-app cancel button (`paypal_cancel_subscription` view) or through PayPal directly. This calls PayPal's cancel API. PayPal fires `BILLING.SUBSCRIPTION.CANCELLED`, but the user still has paid time remaining. The handler calls `mark_subscription_cancelling()` to set `paypal_cancel_at` (from `next_billing_time`, or a 30-day fallback). Premium is NOT removed. Later, `BILLING.SUBSCRIPTION.EXPIRED` fires when the period actually ends, triggering `deactivate_subscription()`.

### Payment Failure & Retry

**Stripe:** `invoice.payment_failed` fires on each failed attempt. `handle_payment_failed()` sends an in-app notification on every attempt (with `next_payment_attempt` timestamp stored in metadata for the admin dashboard). Email is sent only on the first failure and the final warning (when `next_payment_attempt` is None). During `past_due` status, premium features remain active but the `SubscriptionPeriod` is closed to stop milestone time accumulation. If Stripe gives up (status becomes `unpaid`), `deactivate_subscription()` is called.

**PayPal:** No retry cycle. `BILLING.SUBSCRIPTION.SUSPENDED` fires immediately on payment failure. The handler sends a final-warning email and notification, then deactivates premium right away.

**3D Secure / SCA:** `invoice.payment_action_required` fires when a payment needs customer authentication. A notification and email are sent with the Stripe hosted invoice URL where the user can complete verification. Premium stays active during this window.

### activate_subscription() Side Effects

When a subscription becomes active (any provider, any event), `activate_subscription()` runs a defined sequence:

1. Set `premium_tier` and `subscription_provider` on user
2. Update `profile.user_is_premium`
3. Open (or reopen) a `SubscriptionPeriod`
4. Send Discord embed notification (new subscriptions only, not renewals)
5. Assign Discord role via `notify_bot_role_earned()` (deferred via `on_commit`)
6. Send welcome email (new subscriptions only)
7. Check `is_premium` and `subscription_months` milestones

### deactivate_subscription() Side Effects

1. Clear `premium_tier` and `subscription_provider` (plus PayPal-specific fields)
2. Set `profile.user_is_premium = False`
3. Close open `SubscriptionPeriod`
4. Remove Discord role via `notify_bot_role_removed()` (deferred via `on_commit`)
5. Send cancellation email + in-app notification (for voluntary cancellation events only)

## Integration Points

| System | Integration |
|--------|-------------|
| **Discord** | New subscription embed via `send_subscription_notification()`. Role assignment/removal via `notify_bot_role_earned/removed()`. Donation embeds via `queue_webhook_send()`. All deferred via `on_commit`. |
| **Email** | All emails routed through `EmailService.send_html_email()` with `EmailLog` recording. Email preference checks (`EmailPreferenceService.should_send_email()`) gate every email. Suppressed emails are logged via `EmailService.log_suppressed()`. |
| **Notifications** | In-app notifications for: payment failures, payment action required, subscription cancellation, donation receipts, badge claims, artwork completion. All via `NotificationService.create_notification()`. |
| **Milestones** | `activate_subscription()` triggers `check_all_milestones_for_user()` for `is_premium` and `subscription_months` criteria. Donations trigger the "Badge Artwork Patron" manual milestone. |
| **Admin Dashboard** | `/staff/subscriptions/` reads `SubscriptionPeriod`, payment failure notifications (with `next_payment_attempt` metadata), and `EmailLog` records. Admin actions include resend email/notification and force deactivate. |
| **Fundraiser** | `/staff/fundraiser/` manages donation/claim tables. Claim status transitions trigger `send_artwork_complete_email()` and `send_artwork_complete_notification()`. |
| **djstripe** | Stripe events are processed through `DJStripeEvent.process()` for record-keeping. `Subscription`, `Customer`, and `Price` models from djstripe are used for querying Stripe state. |

## Gotchas and Pitfalls

### PayPal Double-Email Guard

PayPal fires `BILLING.SUBSCRIPTION.ACTIVATED` and `PAYMENT.SALE.COMPLETED` in rapid succession on initial subscription. The ACTIVATED handler sends a welcome email; the SALE.COMPLETED handler sends a payment-succeeded email. Without a guard, the user would receive both within seconds. The fix: `PAYMENT.SALE.COMPLETED` checks `EmailLog` for a `subscription_welcome` entry within the last 5 minutes. If found, it skips the payment-succeeded email. This is the only place in the codebase that queries `EmailLog` for deduplication, so be careful adding new email types that fire close together.

### Webhook Routing Order Is Load-Bearing

Donation events MUST be intercepted before subscription events in both webhook handlers. For Stripe, a donation's `checkout.session.completed` would otherwise be passed to `SubscriptionService.handle_webhook_event()`, which would try to look up a subscription for the customer and fail or misfire. For PayPal, `PAYMENT.CAPTURE.COMPLETED` could match a subscription renewal if the donation check is skipped. The routing is: try donation first, return early on match, fall through otherwise.

### PayPal CANCELLED vs. EXPIRED vs. SUSPENDED

These three events have very different meanings:
- **CANCELLED**: User voluntarily cancelled, but has paid time remaining. DO NOT remove premium. Set `paypal_cancel_at` and wait for EXPIRED.
- **EXPIRED**: Paid period ended. Remove premium now.
- **SUSPENDED**: Payment failed. Remove premium immediately (no retry cycle like Stripe).

Getting these wrong either revokes access the user paid for or grants indefinite free access.

### Test vs. Live IDs Must Stay Separate

`users/constants.py` has completely separate product/price/plan ID dictionaries for test and live modes. The mode is determined by `settings.STRIPE_MODE` and `settings.PAYPAL_MODE`. If IDs are mixed (e.g., a live price ID in the test dictionary), webhook events will fail to map to tiers, and `update_user_subscription()` will deactivate the user's subscription due to "unknown product ID." PayPal sandbox plan IDs are currently empty strings (unconfigured), which means PayPal is only testable in live mode.

### ad_free Tier Is Not Premium

The `ad_free` tier exists in `PREMIUM_TIER_CHOICES` and has real Stripe/PayPal products, but `ACTIVE_PREMIUM_TIERS` only includes `premium_monthly`, `premium_yearly`, and `supporter`. The `ad_free` tier removes ads but does not set `profile.user_is_premium = True`, does not grant Discord roles, and does not trigger milestone checks. Code that checks `is_tier_premium()` will return False for `ad_free`.

### SubscriptionPeriod Pause During past_due

When a Stripe subscription enters `past_due` (payment failing, Stripe still retrying), `update_user_subscription()` closes the `SubscriptionPeriod` but keeps premium features active. This prevents milestone time from accumulating during an unpaid window. When payment succeeds (back to `active`), `activate_subscription()` attempts to reopen the most recently closed period (within 14 days) rather than creating a new one. This preserves the user's loyalty streak.

### Donation provider_transaction_id Lifecycle

`Donation.provider_transaction_id` starts as `pending_{uuid}` on creation, then is overwritten with the real Stripe Session ID or PayPal Order ID once the checkout session/order is created. The field has a `unique` constraint, so the temporary UUID prevents collisions during the brief window before the real ID is assigned.

### PayPal Token Caching

PayPal OAuth2 tokens are cached in Redis for 8 hours (tokens are valid for ~9 hours). The cache key includes the PayPal mode (`sandbox`/`live`) to prevent cross-mode token contamination. If Redis is flushed, the next API call will re-acquire a token transparently.

### PayPal Webhook Idempotency

PayPal guarantees at-least-once delivery. The `paypal_webhook()` handler deduplicates by caching `transmission_id` in Redis with a 7-day TTL. Stripe webhooks rely on djstripe's built-in event deduplication (`DJStripeEvent.process()`).

### Discord Side Effects Are Deferred

All Discord API calls (role assignment/removal) are wrapped in `transaction.on_commit()` lambdas. This ensures the database transaction commits before the HTTP call is made, preventing scenarios where a role is assigned but the database save rolled back. The lambdas capture variables by value (`lambda p=profile, r=role_id:`) to avoid late-binding issues.

### PayPal CANCELLED 30-Day Fallback

If the `BILLING.SUBSCRIPTION.CANCELLED` event arrives without a `next_billing_time` (which can happen for certain PayPal edge cases), the handler sets a 30-day fallback expiry. Without this safety net, the user would remain premium indefinitely if the `EXPIRED` webhook never arrives.

## Subscription Tier Reference

| Tier | Grants Premium | Discord Role | Stripe Product (live) | PayPal Plan (live) |
|------|---------------|--------------|----------------------|-------------------|
| `ad_free` | No | None | `prod_ThtXPwe3AD46Au` | `P-51097223GD3632526NGLBPBA` |
| `premium_monthly` | Yes | Premium | `prod_ThsI3EuCssYlTT` | `P-6FE79903U4175840ENGLBP2A` |
| `premium_yearly` | Yes | Premium | `prod_ThsIi3Xd8fY2Hk` | `P-3SY42188DC612830VNGLBQMY` |
| `supporter` | Yes | Supporter (Premium+) | `prod_ThtYQAPoY5pSCN` | `P-5PM309711C131563TNGLBQ3Q` |

## Related Docs

- `../community-hub.md` : Community features that premium unlocks
- `../dashboard.md` : Dashboard system (premium modules)
- `../../CLAUDE.md` : Project conventions including `Concept.absorb()` requirements
