# Supporter Ladder SKUs: Bootstrap and Go-Live

How the six-level supporter ladder ([`SUPPORT_TIERS`](../../users/constants.py)) gets its billing
objects on Stripe and PayPal, and the order of operations that keeps production safe while the
rebuild branch and `main` share the same processor accounts.

## The shape

| Processor | Objects | Why this shape |
|-----------|---------|----------------|
| Stripe | 6 products, 12 prices (2 per product) | Tier recovery is a **product-id** reverse lookup (`get_tier_from_product_id`), so one product per level keeps it interval-free: 6 new map entries, zero code changes to recovery |
| PayPal | 6 catalog products, 12 plans | PayPal plans are per-interval by nature; the flat `PAYPAL_PLAN_TO_TIER` reverse map collapses the interval back out (the tier IS the slug, the interval stays a billing detail) |

## Running it

```bash
python manage.py bootstrap_support_skus                   # both providers, current env mode
python manage.py bootstrap_support_skus --provider stripe
python manage.py bootstrap_support_skus --dry-run         # report only
python manage.py bootstrap_support_skus --live-ok         # required when a mode is live
```

Idempotent by construction, safe to re-run:

- Stripe products are matched on `metadata.pp_ladder_slug`
- Stripe prices on `lookup_key = pp_ladder_{slug}_{interval}` (Stripe enforces uniqueness)
- PayPal products carry our own id `PP-LADDER-{SLUG}` (existence is a plain GET)
- PayPal plans are listed per product and matched on the same `pp_ladder_*` name, with a
  deterministic `PayPal-Request-Id` on create as the belt

Every Stripe object touched is synced into djstripe in the same run. This is load-bearing:
checkout resolves ladder prices with `Price.objects.get(id=...)`, so an id that exists on Stripe
but not in djstripe 500s the moment the placeholder flag flips.

## After it runs: the paste

The command ends with ready-to-paste literal dicts for `STRIPE_LADDER_PRICES[mode]` and
`PAYPAL_LADDER_PLANS[mode]` in [`users/constants.py`](../../users/constants.py). It **never edits
source**: the ids differ per environment and the paste diff is the review. Then:

1. Paste the block(s), replacing the empty-string comprehension for that mode.
2. Once **both** providers are filled for the mode you sell in, flip
   `SUPPORT_TIERS_ARE_PLACEHOLDERS = False`.
3. Test-mode end-to-end: each cycle on each provider, then cancel one and confirm the webhook
   deactivation path.

## ⚠ Why live mode is gated

Stripe and PayPal webhooks fan out to **every** registered endpoint on the account. Production's
`main` build resolves a subscriber's tier by product id and, per its current code, **deactivates
premium** for a subscription whose product it does not recognise. If live ladder SKUs existed
before prod ran ladder-aware code, the first live ladder subscriber's webhook would have prod
revoke the very premium they just paid for.

Hard rule, enforced by the `--live-ok` flag rather than by memory:

- **Test/sandbox bootstrap: anytime.** Test keys → test webhooks → beta endpoint only.
- **Live bootstrap: only at the rebuild cutover**, never before.
- Optional belt before cutover: backport the six-entry `STRIPE_PRODUCTS` /
  `PAYPAL_PLAN_TO_TIER` additions to `main` via the usual main-PR lane, so prod can *recognise*
  ladder products even while it cannot sell them.

## Gotchas and Pitfalls

- **`STRIPE_PRICES` stays legacy-only.** `get_prices_from_stripe` raises on one missing id and its
  caller degrades everything to `{}` on a miss. Right shape for three legacy tiers that must exist
  together; wrong shape for a ladder that fills in one bootstrap run at a time. Ladder ids live in
  `STRIPE_LADDER_PRICES` and resolve through `resolve_ladder_price_id` (returns `None`, never
  raises).
- **`PAYPAL_LADDER_PLANS` is keyed `sandbox`/`live`** (matching `PAYPAL_MODE`), while
  `STRIPE_LADDER_PRICES` is keyed `test`/`live` (matching `STRIPE_MODE`). The paste block prints
  the right key; don't "fix" the asymmetry, it mirrors the settings.
- **Deactivating a plan on PayPal's dashboard does not free its name.** The bootstrap skips
  `INACTIVE` plans when matching, so a re-run after a manual deactivation creates a fresh plan
  rather than resurrecting the dead one. Intentional.
- **Changing a ladder price later**: Stripe prices are immutable. Create a new price (new lookup
  key suffix or transfer the lookup key), paste the new id, and leave the old price attached to
  existing subscriptions — same story as the legacy tiers. PayPal plans support price updates via
  `/update-pricing-schemes`, but a new plan + paste is simpler and keeps parity with Stripe.
- **Refunds** remain a manual lever via the processor dashboards, same as donations.
