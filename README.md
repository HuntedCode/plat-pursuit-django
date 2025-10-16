# Plat Pursuit

A PlayStation trophy tracking community built with Django, Tailwind CSS v4, and DaisyUI.

## Setup

1. **Backend**:
   - Install Python 3.10+ and create a virtualenv: `python -m venv venv` then `venv\Scripts\activate`.
   - Install dependencies: `pip install -r requirements.txt`.
   - Run migrations: `python manage.py migrate`.
   - Start server: `python manage.py runserver`.

2. **Frontend**:
   - Install Node.js and run: `npm install`.
   - Build Tailwind CSS: `npm run build` or watch: `npm run watch`.

3. **Linters**:
   - Python: Run `black .` to format code, `black --check .` to verify.
   - CSS/JS/HTML: Run `npm run format` to format with Prettier, `npm run format:check` to verify.

## Development

- Models in `core/models.py` handle users, trophies, and games with many-to-many relationships.
- Use Celery/Redis (planned) for background API syncs.