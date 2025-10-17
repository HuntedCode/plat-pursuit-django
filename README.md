# Plat Pursuit

A PlayStation trophy tracking community web application built with Django, Tailwind CSS v4, and DaisyUI. Users can register with email/username/password, link their PSN accounts (optional), and view/refresh trophy data for any PSN profile, even unlinked ones. The app supports scalable data storage, periodic API updates, and community features like aggregates (e.g., trophy earn rates).

## Project Status

- **Backend**: Django 5.x with PostgreSQL for relational data (users, profiles, games, trophies). Custom user model with case-insensitive usernames and required email. Models support many-to-many relationships for trophy tracking.
- **Frontend**: Tailwind CSS v4 (standalone CLI) and DaisyUI for responsive, themeable UI. Templates configured for server-side rendering.
- **Linters**: Black (Python) and Prettier (CSS/JS/HTML) ensure consistent code style.
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
   - Visit `http://127.0.0.1:8000/admin/` to manage users, profiles, and trophies.

## Development Notes

### Database Models

- **CustomUser (users app)**: Extends `AbstractUser`. Requires email, unique case-insensitive username, and password. Used for authentication/registration.
- **Profile (trophies app)**: Stores PSN data (`psn_username`, `avatar_url`). Optional `OneToOneField` to `CustomUser` (nullable for unlinked profiles). Created on any PSN lookup.
- **Game**: Stores game metadata (`psn_id`, `title`, `platform`). Uses JSONB for flexible data.
- **Trophy**: Represents trophies (`trophy_id`, `name`, `type` [Bronze/Silver/Gold/Platinum]). Links to `Game` via `ForeignKey`.
- **EarnedTrophy**: Through model for `Profile`-`Trophy` many-to-many. Stores `earned_date` and `last_updated` for delta syncs and aggregates (e.g., earn rates).

**Why This Structure?**

- Separates auth (`users`) from domain logic (`trophies`) for modularity.
- Supports optional PSN linking: Profiles exist for any looked-up PSN ID, linked or not.
- `EarnedTrophy` enables scalable tracking of trophy earns with timestamps, critical for delta-based API syncs and community stats (e.g., `Trophy.earned_by.count()` for earn rates).
- Indexes on `username`, `psn_username`, `trophy_id` ensure fast queries for thousands+ users.

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
