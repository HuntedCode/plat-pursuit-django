# Testing Guide

> How we test PlatPursuit. The test suite is the gate between a bug and production. This guide covers the harness, how to run tests, our conventions, and the priority order for growing coverage.

## Why this exists

Until June 2026 there was no automated testing: code reached production with only a human reading the diff as a safety check. As part of the gamification rebuild we are introducing **end-to-end testing as a standing part of the workflow** — write tests as we build, and never let a change reach `main` (and therefore Render/prod) without the suite passing.

We are growing coverage **spine-first**: the highest-coupling, highest-risk engine code (where a regression silently corrupts real user data) gets tested before the leaves.

## The stack

| Tool | Role |
|---|---|
| `pytest` + `pytest-django` | Test runner + Django integration |
| `factory-boy` | One-line valid model fixtures (`tests/factories.py`) |
| `fakeredis` | In-memory Redis for the raw redis client |
| `responses` | Mock outbound HTTP (PSN / IGDB / Discord) at the boundary |
| `freezegun` | Freeze time for date-sensitive logic |

Config lives in `pyproject.toml` under `[tool.pytest.ini_options]`. Test-only
deps live in `requirements-dev.txt` (kept **out** of the prod Docker image).

## Setup

```bash
# one-time: install test deps into your venv
pip install -r requirements-dev.txt

# start the disposable test Postgres (5544, separate from dev):
docker compose -f docker-compose.test.yml up -d
# optional: a local redis on 6379 so leaderboard writes persist during tests
docker compose up -d redis

# run the suite (zero config: settings_test defaults to localhost:5544)
pytest
```

Tests run against **Postgres, not SQLite** — the engine relies on Postgres-specific
behavior (DB-side aggregation for whale profiles, JSON fields, statement timeouts),
so SQLite would give false confidence. Django creates and tears down a throwaway
`test_plat_pursuit_dev` database inside the `db-test` container automatically.

**Why a separate `db-test` service (not the dev `db`)?** Tests must never touch
your dev data or a remote DB. `settings_test.py` ignores `.env`'s `DATABASE_URL`
(which points at host `db`, only resolvable inside Docker) and instead targets a
dedicated Postgres on port **5544** whose data lives in tmpfs (RAM) and is wiped
on stop. Override any of `TEST_DB_{NAME,USER,PASSWORD,HOST,PORT}` to point
elsewhere (CI sets these for its own Postgres service).

## Running

```bash
pytest                      # whole suite
pytest tests/               # just the cross-cutting harness/spine tests
pytest trophies/            # one app
pytest -k absorb            # tests matching a keyword
pytest path/to/test_x.py::test_name   # a single test
pytest --create-db          # force a fresh test DB (default reuses it for speed)
```

## Conventions

- **Location.** Cross-cutting / engine tests go in the top-level `tests/` package.
  App-specific tests go in that app's `tests/` package (e.g. `trophies/tests/`).
- **Settings.** `plat_pursuit/settings_test.py` inherits prod settings, then
  neutralizes external I/O (locmem cache + email, dummy Stripe key, no HTTPS
  redirect, fast password hasher). Add new neutralizations there, not in tests.
- **DB access.** Mark any test that touches the DB with `@pytest.mark.django_db`.
- **Fixtures.** Prefer the factories (`tests/factories.py`) over hand-built rows.
  Shared fixtures live in the root `conftest.py` (`user`, `profile`, `fake_redis`).
- **External boundaries — never hit the network.** Mock PSN/IGDB/Discord HTTP with
  `responses`, Redis with the `fake_redis` fixture, time with `freezegun`. A test
  that makes a real outbound call is a bug in the test.
- **One behavior per test.** Name it after the behavior (`test_absorb_repoints_ctg_not_just_concept`).

## Coverage priority (spine-first)

Grow the suite outward from the coupling spine identified in the
[system inventory](../design/rebuild/system-inventory.md):

1. `Concept.absorb()` — data migration on concept reassignment (highest blast radius)
2. Sync pipeline (`token_keeper` / `psn_api_service`) — completion detection
3. Badge evaluation (`badge_service`) — tiers, stages, bundles, prerequisites
4. XP (`xp_service`) — calculation + bulk update + signals
5. IGDB matching (`igdb_service`) — the 6-strategy pipeline + enrichment
6. Leaderboards (`redis_leaderboard_service`)

### Regression tests from hard-won bugs

Each documented past bug becomes a permanent "this must never come back" test.
Seed targets (from the project memory + CLAUDE.md):

- ✅ `absorb()` CTG-cascade trap — Review/UserConceptRating must re-point
  `concept_trophy_group` (not just `concept`) and dedup on `trophy_group_id`.
  *(Covered: `tests/engine/test_concept_absorb.py`.)*
- Re-anchor enrichment merge — enrichment must drop (not stack) when the target
  keeps its own IGDBMatch.
- Anchored-concept undo on sync — `add_concept` must respect
  `anchor_migration_completed_at`.
- Whale-OOM — per-profile aggregates must run in the DB, not Python iteration.
- Franchise namespace collapse — `(igdb_id, source_type)` uniqueness.

## Clean slate (legacy tests removed)

The handful of tests that existed before this harness had **rotted** — never run,
so the models/constants they referenced had moved on (removed `TIER_XP_MAP`,
removed `MonthlyRecap` fields, comment-create paths deleted). Rather than rehab
tests of unknown vintage, they were **deleted** (2026-06-07) so the trustworthy
suite is exclusively what we author going forward. That rot is exactly the decay
the gate now prevents.

## The CI gate

Once the spine suite is green, a GitHub Actions workflow (`.github/workflows/tests.yml`)
runs the suite on every push / pull request against a Postgres + Redis service.
A branch-protection rule then blocks merging into `main` while the check is red.
Because Render deploys from `main`, a failing test cannot reach production. *(This
gate is wired after the first spine tests exist — a gate over zero tests is moot.)*

## Gotchas and Pitfalls

- **Postgres required.** A connection error on first run usually means
  `docker compose -f docker-compose.test.yml up -d` wasn't run. A "password authentication failed"
  against the dev `db` (5432) means that container's persisted volume drifted
  from the compose default — another reason tests use the disposable `db-test`.
- **`--reuse-db` staleness.** The test DB is reused for speed. After a new
  migration, run `pytest --create-db` once so the schema rebuilds.
- **Management commands named `test_*`.** Several Django commands are literally
  named `test_*.py` (e.g. `test_email_system`). They are NOT pytest tests; the
  `norecursedirs = [..., "*/management"]` rule keeps pytest from collecting them.
- **Raw Redis vs Django cache.** The Django cache is locmem in tests. The raw
  redis client (token_keeper / leaderboards) is pointed at `localhost` (DB index
  15) via a `REDIS_URL` override in `settings_test.py` — `.env` uses the
  Docker-internal `redis` host, which would hang on DNS timeouts locally. Run
  `docker compose up -d redis` so those writes succeed; if redis is down they
  refuse instantly and are swallowed (leaderboard writes are best-effort).
