# Staging / Beta Environment (`beta.platpursuit.com`)

A staff-only deployment of the **`rebuild`** branch running against a **snapshot of prod
data**, so the team can click through the redesign before it ships. It is deliberately NOT a
backend-testing environment: **no real emails, no live payments, no PSN sync.**

## What it is / isn't

| | |
|---|---|
| Branch | `rebuild` (auto-deploys on push) |
| Data | A restored **snapshot** of the prod DB — frozen (no sync worker runs) |
| Access | **Staff only** — anonymous → login, non-staff → 403 (`BetaStaffGateMiddleware`) |
| Email | Dummy backend — nothing ever sends |
| Payments / Ads | Inert — Stripe/PayPal default to test/sandbox, AdSense off |
| PSN sync | **Off** — no TokenKeeper worker, so no token contention with prod |
| Crawlers | `noindex, nofollow` on every response |

## The code side (already on `rebuild`, inert on prod)

Everything is gated on the **`BETA`** env var (`settings.IS_BETA = os.getenv('BETA') == '1'`):
- `BetaStaffGateMiddleware` — the staff gate + `noindex`; answers `/healthz/` for Render's check.
- Email is forced to the **dummy backend** when `IS_BETA` (so `DEBUG=False` can't reach SendGrid).
- `CloudflareOriginGuardMiddleware` **skips** when `IS_BETA` — otherwise game/badge detail pages
  (which lack a `CF-Ray` header off-Cloudflare) would 302 to `https://platpursuit.com`.

With `BETA` unset, all of the above is a no-op, so this is safe on production.

## One-time setup (Render dashboard + DNS)

1. **Postgres** — create a new Render Postgres, then **restore from a prod backup/snapshot**.
   (Optional PII scrub after restore: null out non-staff `auth_user.email`. Staff-gated, so low risk.)
2. **Key Value (Redis)** — create a new instance (isolated from prod's).
3. **Web Service** — connect the repo, **Branch = `rebuild`**, **Auto-Deploy = On**.
   - **Build / Start / Pre-Deploy commands: copy them from the prod web service** (there's no
     `render.yaml`, so prod is configured in the dashboard). The Pre-Deploy (or build) MUST run
     `python manage.py migrate` — that's what creates the `pg_trgm` extension + all `rebuild`
     migrations on the beta DB — and `collectstatic` for WhiteNoise.
   - **Health Check Path = `/healthz/`**.
   - **Do NOT create a background worker** (that's the PSN sync worker — we want it off).
4. **Env vars** — set the table below (an Env Group you attach to the service is tidiest).
5. **First deploy** — watch the migrate step: it runs `CREATE EXTENSION pg_trgm` + the concurrent
   trigram index builds (see `docs/design/rebuild/prod-deploy-checklist.md` item E). Render's
   Postgres role can create `pg_trgm`.
6. **Custom domain** — add `beta.platpursuit.com` to the service, create the CNAME at your DNS
   registrar, wait for the auto-TLS cert.
7. **Make yourself staff** — your user in the snapshot needs `is_staff=True`. If not, open the
   Render Shell: `python manage.py shell` → set `is_staff` on your `User`, or use the admin.

## Env vars

**Set these:**

| Var | Value | Why |
|---|---|---|
| `BETA` | `1` | Turns on the staff gate + `noindex` + dummy email + CF-guard skip |
| `SECRET_KEY` | any strong value | Required; a fresh random string is fine |
| `DATABASE_URL` | beta Postgres internal URL | Isolated DB (never point at prod) |
| `REDIS_URL` | beta Key Value internal URL | Isolated cache / queue |
| `ALLOWED_HOSTS` | `beta.platpursuit.com` | (add the `…onrender.com` host too while first testing) |
| `CSRF_TRUSTED_ORIGINS` | `https://beta.platpursuit.com` | Login/form POSTs |
| `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_ACCESS_KEY_ID`, `AWS_S3_SECRET_ACCESS_KEY`, `AWS_S3_REGION_NAME` | **reuse prod values** | Media (badge medallions!) is on S3; without these it won't load |

**Do NOT set (so they stay inert by default):**

| Var | Left unset → | 
|---|---|
| `DEBUG` | `False` (beta mirrors prod) |
| `STRIPE_MODE` | `test` (test/empty keys, no charges) |
| `PAYPAL_MODE` | `sandbox` |
| `ADSENSE_ENABLED` | `False` (ads off) |
| `SENDGRID_API_KEY` | n/a (dummy email backend) |
| Discord webhooks / `BOT_API_KEY` | omitted (no PlatBot posts) |

`IGDB_CLIENT_ID` / `IGDB_CLIENT_SECRET` are optional — stored cover IDs render without a live call;
only needed if you run enrichment (you won't on beta).

## Refreshing the data

The snapshot is frozen. When staff want fresher data, restore a new prod backup into the beta
Postgres and redeploy. Weekly-ish is plenty for redesign review.

## Gotchas and Pitfalls

- **Never point `DATABASE_URL` at the prod DB.** `rebuild` carries migrations not on `main`;
  running `migrate` against prod would apply unreleased schema to production. The whole point of a
  separate beta DB is to run those migrations safely.
- **`SITE_URL` is hardcoded to `https://platpursuit.com`** (settings.py:22). Share-card / canonical /
  sitemap absolute links on beta therefore point at prod. Cosmetic for review — leave it.
- **djstripe at boot:** if the service fails to start referencing Stripe, set `STRIPE_TEST_SECRET_KEY`
  + `STRIPE_TEST_PUBLISHABLE_KEY` to your Stripe **test** keys (free; still no real charges).
- **The 2–3 payment pages** (fundraiser, subscriptions) may show an uninitialized checkout button on
  empty test keys — everything else is unaffected. Drop in prod's *publishable* (public) key if you
  want them to render fully; a charge only happens on an actual completed submit.
- **No crons on beta.** Data is frozen, and the search bar needs none (trigram indexes self-maintain,
  the suggest cache is on-demand). Scheduling `recalc_earn_rates` etc. against a snapshot is pointless.
- **Beta is not behind Cloudflare** by design — the CF origin guard skips on `IS_BETA`, so you don't
  need to proxy `beta.` through Cloudflare.
