# Settings Overview

Key Django settings, third-party integrations, and constants files used across PlatPursuit. For environment variable setup, see [Local Setup](../guides/local-setup.md).

## Django Settings (`plat_pursuit/settings.py`)

### Key Configuration

| Setting | Value | Notes |
|---------|-------|-------|
| `DJANGO_SETTINGS_MODULE` | `plat_pursuit.settings` | Single settings file (no dev/prod split) |
| `DEBUG` | Env var | Controls email backend, error display, payment completion flow |
| `DATABASES` | PostgreSQL 15 | `DATABASE_URL` takes priority over individual `DB_*` vars |
| `CACHES` | `django-redis` | Single cache backend at `REDIS_URL` |
| `EMAIL_BACKEND` | SendGrid (prod) / Console (debug) | `django-sendgrid-v5` in production |
| `STATIC_FILES_STORAGE` | WhiteNoise | S3 via `django-storages` optional |

### Third-Party Integrations

| Service | Config Vars | Package |
|---------|-------------|---------|
| Stripe | `STRIPE_MODE`, `STRIPE_*_PUBLISHABLE_KEY`, `STRIPE_*_SECRET_KEY`, `STRIPE_*_WEBHOOK_SECRET` | `stripe`, `dj-stripe` |
| PayPal | `PAYPAL_MODE`, `PAYPAL_*_CLIENT_ID`, `PAYPAL_*_CLIENT_SECRET`, `PAYPAL_*_WEBHOOK_ID` | Custom service |
| SendGrid | `SENDGRID_API_KEY` | `django-sendgrid-v5` |
| Sentry | Auto-configured | `sentry-sdk` |
| AWS S3 | `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_ACCESS_KEY_ID`, `AWS_S3_SECRET_ACCESS_KEY` | `django-storages`, `boto3` |
| Discord | `BOT_API_URL`, `BOT_API_KEY`, `DISCORD_*_WEBHOOK_URL`, `DISCORD_*_ROLE` | Custom service |
| AdSense | `ADSENSE_PUB_ID`, `ADSENSE_ENABLED`, `ADSENSE_TEST_MODE` | Context processor |
| CORS | `CORS_ALLOWED_ORIGINS` | `django-cors-headers` (mobile app) |

### Payment Mode Switching

Stripe and PayPal each have test/live modes controlled by `STRIPE_MODE` and `PAYPAL_MODE` env vars. Each mode has its own set of keys, secrets, and webhook IDs. The settings file selects the correct set based on the mode value.

## Constants Files

### trophies/util_modules/constants.py

The primary constants file (~235 lines). Contains:

**Platform & Region**

| Constant | Value | Purpose |
|----------|-------|---------|
| `MODERN_PLATFORMS` | `['PS5', 'PS4']` | Platforms with title stats support |
| `ALL_PLATFORMS` | `['PS5', 'PS4', 'PS3', 'PSVITA', 'PSVR']` | Full platform list |
| `NA_REGION_CODES` | `['IP', 'UB', 'UP', 'US', 'UT']` | North American title ID prefixes |
| `EU_REGION_CODES` | `['EB', 'EP']` | European prefixes |
| `JP_REGION_CODES` | `['JA', 'JB', 'JP', 'KP']` | Japanese prefixes |
| `AS_REGION_CODES` | `['HA', 'HB', 'HP', 'HT']` | Asian prefixes |

**Badge XP**

| Constant | Value | Purpose |
|----------|-------|---------|
| `BRONZE_STAGE_XP` | 250 | XP per concept at Bronze tier |
| `SILVER_STAGE_XP` | 75 | XP per concept at Silver tier |
| `GOLD_STAGE_XP` | 250 | XP per concept at Gold tier |
| `PLAT_STAGE_XP` | 75 | XP per concept at Platinum tier |
| `BADGE_TIER_XP` | 3000 | XP for completing a full badge |

**Genre Challenge**

- `GENRE_CHALLENGE_GENRES`: 16 curated genres from PSN API data
- `GENRE_DISPLAY_NAMES`: Human-readable names for each genre
- `GENRE_MERGE_MAP`: Maps PSN API genres to curated keys (e.g., `SIMULATOR` to `SIMULATION`)
- `GENRE_CHALLENGE_SUBGENRES`: 32 curated subgenres for bonus tracker
- `SUBGENRE_MERGE_MAP`: Maps PSN API subgenres to curated keys

**Other**

- `TITLE_ID_BLACKLIST`: Games with known issues (excluded from sync)
- `SEARCH_ACCOUNT_IDS`: Special PSN accounts used for search/development
- `COMMUNITY_GUIDELINES`: 9-item list of community rules shown to users
- `BANNED_WORDS`: Managed through Django admin (database), template list only

### Key Settings Gotchas

- **Test/live mode isolation**: Stripe and PayPal keys are completely separate per mode. Mixing test keys with live webhook secrets will silently fail webhook verification.
- **DATABASE_URL priority**: If `DATABASE_URL` is set, individual `DB_*` vars are ignored. Cannot mix them.
- **Redis single instance**: Both Django cache and raw Redis (Token Keeper) use the same Redis instance but different databases/key prefixes.
- **CORS is mobile-only**: Only needed for the React Native Expo dev server. Web app uses same-origin.

## Related Docs

- [Local Setup](../guides/local-setup.md): Full environment variable reference
- [Payment Webhooks](../architecture/payment-webhooks.md): Stripe/PayPal mode configuration
- [Gamification](../architecture/gamification.md): Badge XP constants usage
- [Challenge Systems](../features/challenge-systems.md): Genre/subgenre constants usage
