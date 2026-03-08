# Local Development Setup

How to get PlatPursuit running locally for development. The stack is Django 5.2.7 + PostgreSQL 15 + Redis 7, with a separate Token Keeper worker process for PSN sync jobs.

## Prerequisites

- Python 3.12+
- PostgreSQL 15+
- Redis 7+
- Node.js (for Tailwind CSS builds)
- Playwright Chromium (for share card rendering, optional)

## Quick Start (Docker)

The `docker-compose.yml` provides all services:

```bash
# Start everything (web, worker, db, redis)
docker compose up

# Or run specific services
docker compose up db redis       # Infrastructure only
docker compose up web            # Django dev server
docker compose up worker         # Token Keeper worker
```

Services:
- **web**: Django dev server on `http://localhost:8000`
- **worker**: Token Keeper sync pipeline (`python manage.py start_token_keeper`)
- **db**: PostgreSQL 15 on port 5432 (data persisted in `postgres_data` volume)
- **redis**: Redis 7 on port 6379

## Manual Setup (Without Docker)

### 1. Clone and Install Dependencies

```bash
git clone <repo-url>
cd PlatPursuit
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
```

### 2. Install Playwright (Optional)

Required only for server-side share card PNG rendering:

```bash
playwright install chromium
```

On Linux, you may also need Chromium dependencies (see `Dockerfile` for the full list).

### 3. Create `.env` File

Copy from the example below and fill in values:

```env
# === Required ===
SECRET_KEY=your-django-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000

# === Database ===
# Option A: Connection URL (takes priority)
DATABASE_URL=postgres://plat_user:securepass@localhost:5432/plat_pursuit_dev
# Option B: Individual fields (used if DATABASE_URL is not set)
DB_NAME=plat_pursuit_dev
DB_USER=plat_user
DB_PASSWORD=securepass
DB_HOST=localhost
DB_PORT=5433

# === Redis ===
REDIS_URL=redis://localhost:6379/1

# === Stripe (test mode) ===
STRIPE_MODE=test
STRIPE_TEST_PUBLISHABLE_KEY=pk_test_...
STRIPE_TEST_SECRET_KEY=sk_test_...
STRIPE_TEST_WEBHOOK_SECRET=whsec_...

# === PayPal (sandbox) ===
PAYPAL_MODE=sandbox
PAYPAL_SANDBOX_CLIENT_ID=...
PAYPAL_SANDBOX_CLIENT_SECRET=...
PAYPAL_SANDBOX_WEBHOOK_ID=...

# === Email (SendGrid) ===
SENDGRID_API_KEY=SG....

# === Discord Bot (optional) ===
BOT_API_URL=http://127.0.0.1:5000
BOT_API_KEY=...
DISCORD_PLATINUM_WEBHOOK_URL=...
DISCORD_TEST_WEBHOOK_URL=...
PLATINUM_EMOJI_ID=...
PLAT_PURSUIT_EMOJI_ID=...
DISCORD_PREMIUM_ROLE=...
DISCORD_PREMIUM_PLUS_ROLE=...

# === AWS S3 (optional, for static/media in production) ===
AWS_STORAGE_BUCKET_NAME=...
AWS_S3_ACCESS_KEY_ID=...
AWS_S3_SECRET_ACCESS_KEY=...
AWS_S3_REGION_NAME=us-west-2

# === Ads (optional) ===
ADSENSE_PUB_ID=...
ADSENSE_ENABLED=False
ADSENSE_TEST_MODE=False

# === Mobile CORS (optional) ===
CORS_ALLOWED_ORIGINS=http://localhost:19006
```

### 4. Database Setup

```bash
python manage.py migrate
python manage.py createsuperuser
```

### 5. Seed Data (Optional)

```bash
python manage.py populate_milestones     # Milestone definitions
python manage.py populate_stat_types     # Gamification stat types
```

### 6. Run the Dev Server

```bash
python manage.py runserver
```

### 7. Run Token Keeper Worker (Separate Terminal)

```bash
python manage.py start_token_keeper
```

### 8. Build Tailwind CSS

```bash
npx tailwindcss -i ./static/css/input.css -o ./static/css/output.css --watch
```

## Environment Variable Reference

### Required

| Variable | Purpose | Default |
|----------|---------|---------|
| `SECRET_KEY` | Django secret key | None (required) |
| `DEBUG` | Debug mode | `False` |
| `ALLOWED_HOSTS` | Comma-separated hostnames | `localhost,127.0.0.1` |

### Database

| Variable | Purpose | Default |
|----------|---------|---------|
| `DATABASE_URL` | Full connection string (takes priority) | None |
| `DB_NAME` | Database name | `plat_pursuit_dev` |
| `DB_USER` | Database user | `plat_user` |
| `DB_PASSWORD` | Database password | `securepass` |
| `DB_HOST` | Database host | `localhost` |
| `DB_PORT` | Database port | `5433` |

### Redis

| Variable | Purpose | Default |
|----------|---------|---------|
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/1` |
| `NOTIFICATION_CACHE_ENABLED` | Enable notification caching | `True` |

### Payments

| Variable | Purpose |
|----------|---------|
| `STRIPE_MODE` | `test` or `live` |
| `STRIPE_TEST_PUBLISHABLE_KEY` | Stripe test public key |
| `STRIPE_TEST_SECRET_KEY` | Stripe test secret key |
| `STRIPE_TEST_WEBHOOK_SECRET` | Stripe test webhook secret |
| `PAYPAL_MODE` | `sandbox` or `live` |
| `PAYPAL_SANDBOX_CLIENT_ID` | PayPal sandbox client ID |
| `PAYPAL_SANDBOX_CLIENT_SECRET` | PayPal sandbox secret |
| `PAYPAL_SANDBOX_WEBHOOK_ID` | PayPal sandbox webhook ID |

## Production Deployment (Render)

The production environment runs on Render with:
- **Web service**: Gunicorn with gevent workers
- **Worker service**: Token Keeper (`python manage.py start_token_keeper`)
- **Database**: Render PostgreSQL
- **Redis**: Render Redis
- **Static files**: WhiteNoise (or S3 via django-storages)
- **Cron jobs**: Render Cron Jobs (see [Cron Jobs](cron-jobs.md))

## Related Docs

- [Management Commands](management-commands.md): All available commands
- [Cron Jobs](cron-jobs.md): Scheduled task configuration
- [Email Setup](email-setup.md): SendGrid and Cloudflare email configuration
- [Settings Overview](../reference/settings-overview.md): Key settings and constants
