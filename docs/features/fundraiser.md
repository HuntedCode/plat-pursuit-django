# Fundraiser System

A reusable campaign framework for one-time donations with optional reward mechanics. Currently implemented for the **Badge Artwork Fundraiser**, where donors earn "badge picks" ($10 each) to claim badge series for custom artwork commissioning. The system supports date-driven lifecycle states, dual payment providers (Stripe + PayPal), and a staff admin dashboard.

## Architecture Overview

The fundraiser lives in its own Django app (`fundraiser/`) with a `campaign_type` discriminator on the `Fundraiser` model. This means future campaign types (e.g., server fundraiser, feature voting) can be added without schema changes: the discriminator gates all reward mechanics.

Payment uses **one-time checkout** (not subscriptions). Stripe uses `mode='payment'` sessions, PayPal uses Orders API v2 with `intent='CAPTURE'`. Both providers route through the existing webhook handlers in `users/views.py`, where donation events are intercepted BEFORE subscription handlers to prevent false subscription activations.

The lifecycle is date-driven: `is_upcoming()`, `is_live()`, `is_ended()` methods on the Fundraiser model control access. Staff can preview anytime. Non-staff are redirected if upcoming, and see a read-only archive if ended. A dismissible site-wide banner (controlled by `banner_active` + `is_live()`) drives awareness.

Badge claiming uses `select_for_update()` on the Badge row to prevent race conditions (two donors claiming the same badge simultaneously). The `DonationBadgeClaim` model uses a `OneToOneField` to Badge, enforcing one claim per badge series at the database level.

## File Map

| File | Purpose |
|------|---------|
| `fundraiser/models.py` | Fundraiser, Donation, DonationBadgeClaim models (199 lines) |
| `fundraiser/services/donation_service.py` | Payment flows, claiming, emails, notifications (746 lines) |
| `fundraiser/views.py` | FundraiserView, DonationSuccessView, FundraiserAdminView (407 lines) |
| `api/fundraiser_views.py` | CreateDonation, ClaimBadge, UpdateClaimStatus APIs (204 lines) |
| `static/js/fundraiser.js` | FundraiserPage + FundraiserAdmin (496 lines) |
| `templates/fundraiser/fundraiser.html` | Main campaign page (456 lines) |
| `templates/fundraiser/fundraiser_admin.html` | Staff dashboard (252 lines) |
| `templates/fundraiser/partials/badge_tracker.html` | Progress bar + stats |
| `templates/fundraiser/partials/badge_picker_modal.html` | Badge selection modal |
| `templates/fundraiser/partials/donor_wall.html` | Donor display list |
| `templates/emails/donation_receipt.html` | Receipt email |
| `templates/emails/badge_claim_confirmation.html` | Claim confirmation email |
| `templates/emails/artwork_complete.html` | Artwork delivery notification email |
| `plat_pursuit/context_processors.py` | `active_fundraiser()` for banner (60s cache) |
| `templates/partials/fundraiser_banner.html` | Site-wide dismissible banner |

## Data Model

### Fundraiser
- `slug` (unique): URL identifier
- `campaign_type` (choices): Determines reward mechanics (currently: `badge_artwork`)
- `name`, `description`, `start_date`, `end_date` (nullable for perpetual campaigns)
- `banner_active`, `banner_text`, `banner_dismiss_days`: Site-wide banner control
- `minimum_donation` (Decimal), `BADGE_PICK_DIVISOR` ($10): Campaign constants
- Methods: `is_upcoming()`, `is_live()`, `is_ended()`, `show_banner()`

### Donation
- FK to `Fundraiser`, `User`, `Profile` (denormalized for donor wall)
- `amount` (Decimal), `provider` (stripe/paypal), `provider_transaction_id` (unique)
- `status` (pending/completed/failed/refunded)
- `badge_picks_earned` (cumulative: incremental picks from `floor(cumulative_total / 10) - prior_picks_earned`), `badge_picks_used`: For badge_artwork campaigns
- `is_anonymous`, `message`: Donor wall customization
- `metadata` (JSONField): Extensible payment data
- Property: `badge_picks_remaining` = `max(0, earned - used)`

### DonationBadgeClaim
- FK to `Donation`, `Profile`
- `badge` (OneToOneField): Enforces one claim per badge series (Tier 1 only)
- `series_slug`, `series_name`: Denormalized at claim time (survives badge deletion)
- `status` (claimed/in_progress/completed)
- `claimed_at`, `completed_at`

## Key Flows

### Donation Flow

1. User submits donation form with amount, provider, anonymous flag, message
2. `CreateDonationView` validates: fundraiser is live, amount in range ($min-$500)
3. Calls `DonationService.create_stripe_checkout()` or `create_paypal_order()`
4. Service creates `Donation` (status=pending), returns provider checkout URL
5. User completes payment on provider's hosted page
6. Provider sends webhook to `stripe_webhook` or `paypal_webhook`
7. Webhook calls `DonationService.complete_donation()`:
   - Updates status to completed, sets `completed_at`
   - Calculates `badge_picks_earned` cumulatively: `floor(cumulative_total / BADGE_PICK_DIVISOR) - prior_picks_earned` (remainder dollars carry over across donations)
   - Grants "Badge Artwork Patron" milestone + Discord role (first donation)
   - Sends receipt email, in-app notification, Discord webhook
8. User redirected to `DonationSuccessView` (also attempts completion as backup)

### Badge Claiming Flow

1. User opens badge picker modal, selects unclaimed Tier 1 badge
2. `ClaimBadgeView` validates: user owns donation, picks remaining, badge unclaimed
3. `DonationService.claim_badge()` in atomic transaction:
   - `select_for_update()` on Badge row (race protection)
   - Creates `DonationBadgeClaim` (IntegrityError caught for concurrent claims)
   - Decrements `badge_picks_used` via `F()` expression
4. Sends claim confirmation email + in-app notification
5. Frontend reloads page to reflect updated picks

### Admin Artwork Completion

1. Artist uploads badge artwork (external to this system)
2. Staff updates claim status to `completed` via admin dashboard dropdown
3. `UpdateClaimStatusView` sets `completed_at`, updates all badges in series with `funded_by`
4. Sends artwork complete email + notification to donor

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/fundraiser/<slug>/donate/` | Yes | Create Stripe/PayPal checkout |
| POST | `/api/v1/fundraiser/claim/` | Yes | Claim a badge series |
| POST | `/api/v1/admin/fundraiser/claim-status/` | Staff | Update claim status |

## Integration Points

- [Payment Webhooks](../architecture/payment-webhooks.md): Donation events intercepted BEFORE subscription handlers in both `stripe_webhook` and `paypal_webhook`. Session `metadata.type='fundraiser_donation'` filters Stripe events.
- [Notification System](../architecture/notification-system.md): `system_alert` notification type for donation/claim/artwork notifications. Rich sections with headers, icons, actions.
- [Badge System](../architecture/badge-system.md): `DonationBadgeClaim.badge` OneToOneField. Milestone "Badge Artwork Patron" (criteria_type='manual').
- [Email System](../guides/email-setup.md): 3 email templates via EmailService with EmailLog tracking (donation_receipt, badge_claim_confirmation, artwork_complete).
- Discord: Green embeds via `queue_webhook_send()` for donation announcements.
- Context processor: `active_fundraiser()` provides banner data to all pages (60s cache).

## Gotchas and Pitfalls

- **Webhook routing order**: Donation events MUST be checked before subscription events. Both Stripe and PayPal handlers check for fundraiser donations first and return early if matched.
- **DEBUG mode payment completion**: In development, `DonationSuccessView` calls `complete_donation()` on redirect because webhooks cannot reach localhost. This is disabled in production.
- **Badge claim race condition**: `select_for_update()` + `OneToOneField` IntegrityError handling prevents double-claiming. Do not remove either guard.
- **Denormalized series data on claims**: `series_name` and `series_slug` are stored at claim time because the Badge/series could theoretically be deleted or renamed later.
- **Milestone idempotency**: `_grant_fundraiser_milestone()` delegates to the shared `award_manual_milestone()` service which uses `get_or_create()` and only increments `Milestone.earned_count` on first claim. Safe for repeat donors.
- **PayPal nested response structure**: Capture data is deeply nested in `purchase_units[0].payments.captures[0]`. The `custom_id` field links back to the Donation.
- **Badge picks are Tier 1 only**: Users can only claim badges at their base tier (Tier 1). Validated in `claim_badge()`.
- **Anonymous donations**: `is_anonymous` hides identity on donor wall but admin dashboard always shows full donor info.
- **Banner dismiss**: Uses localStorage key `fundraiser_banner_dismissed_{slug}` with configurable dismiss duration.
- **Cumulative pick calculation**: `badge_picks_earned` is computed cumulatively across all of a user's completed donations to a fundraiser, not per-donation. Remainder dollars carry over (e.g., $25 + $5 = $30 = 3 picks, not 2 + 0). The calculation uses `select_for_update()` to prevent race conditions when two donations complete simultaneously. If a donation is refunded after its remainder contributed to a subsequent donation's picks, the pick counts may become inconsistent (manual correction needed).
- **Data correction**: Use `python manage.py fix_badge_picks` to retroactively recalculate picks for users with multiple donations. Always run with `--dry-run` first (default), then `--apply`.

## Related Docs

- [Payment Webhooks](../architecture/payment-webhooks.md): Stripe and PayPal webhook routing
- [Badge System](../architecture/badge-system.md): Badge tiers, milestones
- [Notification System](../architecture/notification-system.md): Notification creation patterns
- [Subscription Lifecycle](subscription-lifecycle.md): Contrasts recurring subscription payments with one-time donations
