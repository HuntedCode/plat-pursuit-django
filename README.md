# Plat Pursuit

A PlayStation trophy tracking community web application built with Django, Tailwind CSS v4, and DaisyUI. Users can register with email/username/password, link their PSN accounts (optional), and view/refresh trophy data for any PSN profile, even unlinked ones. The app supports scalable data storage, periodic API updates, and community features like aggregates (e.g., trophy earn rates).

## Project Status

- **Backend**: Django 5.x with PostgreSQL for relational data (users, profiles, games, trophies). Custom user model with case-insensitive usernames and required email. Models support many-to-many relationships for trophy tracking. Fixed reverse accessor clash in EarnedTrophy (changed related_name to 'earned_trophy_entries') and Profile (renamed earned_trophies to earned_trophy_summary).
- **Frontend**: Tailwind CSS v4 (standalone CLI) and DaisyUI for responsive, themeable UI. Templates configured for server-side rendering.
- **Linters**: Black (Python) and Prettier (CSS/JS/HTML) ensure consistent code style.
- **Admin**: Configured for all models (`CustomUser`, `Profile`, `Game`, `UserGame`, `Trophy`, `EarnedTrophy`) with search, filters, manual linking actions, and computed displays (e.g., trophy counts).
- **Models**: Refined for psnawp data (e.g., added `account_id`, `about_me` to Profile; new `UserGame` for per-user stats; fields like `trophy_rarity`, `progress` in Trophy/EarnedTrophy).
- **Next Steps**: Implement PSN API integration with `psnawp`, background syncs via Celery/Redis, and registration/linking views.

## Tech Stack

- **Backend**: Python 3.10+, Django 5.x, PostgreSQL 16+, Celery (planned for async tasks), Redis (planned for queue/caching).
- **Frontend**: Tailwind CSS v4, DaisyUI, Django templates (HTML/CSS/JS).
- **Tools**: Black, Prettier, Git, npm for frontend builds.
- **Environment**: Windows (dev), with cloud-ready setup for production (e.g., AWS RDS, Heroku).

## Setup Instructions

### Prerequisites

- Python 3.10+ ([python.org](https://www.python.org/downloads/)) - Ensure "Add to PATH" during install.
- Node.js 18+ ([nodejs.org](https://nodejs.org/)) - For Tailwind/DaisyUI builds.
- PostgreSQL 16+ ([postgresql.org](https://www.postgresql.org/download/windows/)) - Install with pgAdmin.
- Git ([git-scm.com](https://git-scm.com/downloads)).
- Redis (Windows port: [github.com/microsoftarchive/redis](https://github.com/microsoftarchive/redis/releases)) - For future Celery tasks.

### Installation

1. **Clone the Repository**:

   ```bash
   git clone git@github.com:HuntedCode/plat-pursuit-django.git
   cd PlatPursuit
   ```

2. **Backend Setup**:
   - Create and activate virtual environment:
     ```bash
     python -m venv venv
     venv\Scripts\activate  # On Windows
     ```
   - Install dependencies:
     ```bash
     pip install -r requirements.txt
     ```
   - Set up PostgreSQL:
     - In pgAdmin, create database `plat_pursuit_dev` and user `plat_user` with password (e.g., `securepass`).
     - Create `.env` in root (add to `.gitignore`):
       ```
       DB_NAME=plat_pursuit_dev
       DB_USER=plat_user
       DB_PASSWORD=securepass
       DB_HOST=localhost
       DB_PORT=5432
       ```
   - Run migrations:
     ```bash
     python manage.py makemigrations
     python manage.py migrate
     ```
   - Create superuser for admin:
     ```bash
     python manage.py createsuperuser
     ```
   - Start server:
     ```bash
     python manage.py runserver
     ```

3. **Frontend Setup**:
   - Install npm dependencies:
     ```bash
     npm install
     ```
   - Build Tailwind CSS:
     ```bash
     npm run build  # Or npm run watch for development
     ```
   - Static files are served from `static/css/output.css`.

4. **Linters**:
   - Format Python with Black:
     ```bash
     black .
     ```
   - Format CSS/JS/HTML with Prettier:
     ```bash
     npm run format
     ```
   - Verify formatting:
     ```bash
     black --check .
     npm run format:check
     ```

5. **Access Admin**:
   - Visit `http://127.0.0.1:8000/admin/` to manage users, profiles, games, trophies, and earned trophies.
   - Features: Search by username/PSN ID/account ID, filter by trophy type or sync tier, link profiles to users manually, view earned trophy counts and per-user game stats.

## Development Notes

### Database Models

- **CustomUser (users app)**: Extends `AbstractUser`. Requires email, unique case-insensitive username, and password. Used for authentication/registration. No automatic profile creation on signup.
- **Profile (trophies app)**: Stores PSN data (`psn_username`, `account_id`, `np_id`, `avatar_url`, `about_me`, `languages_used`, `is_plus`, `trophy_level`, `progress`, `tier`, `earned_trophy_summary`, `extra_data`). Optional `OneToOneField` to `CustomUser` (nullable for unlinked profiles). Created only on PSN lookup with validation (3-16 chars, letters/numbers/hyphens/underscores). Renamed `earned_trophies` to `earned_trophy_summary` (JSON summary of earned counts) to resolve ORM accessor clash with M2M `related_name`.
- **Game (trophies app)**: Stores game metadata (`np_communication_id`, `np_service_name`, `trophy_set_version`, `title_name`, `title_detail`, `title_icon_url`, `title_platform`, `has_trophy_groups`, `defined_trophies`, `np_title_id`, `metadata` for IGDB extras like developer/summary).
- **UserGame (trophies app)**: Through model for per-user game data (`profile`, `game`, `play_count`, `first_played_date_time`, `last_played_date_time`, `play_duration`, `progress`, `hidden_flag`, `earned_trophies`, `last_updated_datetime`).
- **Trophy (trophies app)**: Represents trophies (`trophy_set_version`, `trophy_id`, `trophy_hidden`, `trophy_type`, `trophy_name`, `trophy_detail`, `trophy_icon_url`, `trophy_group_id`, `progress_target_value`, `reward_name`, `reward_img_url`, `trophy_rarity`, `trophy_earn_rate`, `earn_rate`). Links to `Game` via `ForeignKey`.
- **EarnedTrophy (trophies app)**: Through model for `Profile`-`Trophy` many-to-many (`profile`, `trophy`, `earned`, `progress`, `progress_rate`, `progressed_date_time`, `earned_date_time`, `last_updated`). Uses `related_name='earned_trophy_entries'` to avoid clashes with `Trophy.earned_trophies`.

**Why This Structure?**

- Separates auth (`users`) from domain logic (`trophies`) for modularity.
- Supports optional PSN linking: Profiles are created only on PSN lookup and linked explicitly via user action (e.g., form or OAuth).
- All PSN data is retained for community features (e.g., public profiles, global earn rates).
- `UserGame` and `EarnedTrophy` enable scalable per-user tracking with timestamps, critical for delta-based API syncs and stats.
- Indexes on key fields (e.g., `psn_username`, `account_id`, `trophy_id`) ensure fast queries for thousands+ users.
- Validation on `psn_username` enforces PSN format for reliable API calls.
- Prepares for IGDB integration in `Game.metadata` for non-trophy details (e.g., developers, genres).
- Resolved ORM clashes by renaming `Profile.earned_trophies` to `earned_trophy_summary` and setting `EarnedTrophy.related_name='earned_trophy_entries'`.

### PSN API Integration

Uses psnawp v3.0.0 for endpoints like profiles, trophy summaries, and trophies. Syncs delta-based to minimize calls, storing in models like Profile (`account_id`, `about_me`, `trophy_level`), UserGame (`play_duration`, `progress`), and Trophy/EarnedTrophy (`trophy_rarity`, `progress`). Prepares for IGDB metadata in `Game.metadata`. Model structure optimized with delta fields (e.g., `last_updated_datetime`, `earned_trophy_summary`) to reduce API calls.

### Planned Features

- **PSN API**: Use `psnawp` for trophy syncing. Background tasks via Celery/Redis for periodic/delta updates (2-6 hours for preferred users, 24 hours for others).
- **Frontend**: DaisyUI-themed UI with light/dark modes. Registration/linking forms in progress.
- **Aggregates**: Calculate trophy earn rates (stored in `Trophy.earn_rate`) via scheduled tasks.
- **Search/Filtering**: Leverage PostgreSQL full-text search for trophy/game queries.

## Contributing

- Run linters before commits: `black . && npm run format`.
- Add tests in `users/tests.py` and `trophies/tests.py` (planned).
- Document new features in this README.

## License

TBD (e.g., MIT for open-source portfolio).
