# Anava Clinic Backend

FastAPI modular monolith. See `../Documents/Anava_Backend_Architecture_v1.md` for the full design.

## Stack

Python 3.12 · FastAPI · async SQLAlchemy 2.0 + asyncpg · Alembic · PostgreSQL 16 · Redis · SQS (ElasticMQ locally) · Razorpay · AWS Cognito/S3 (Stage 13+)

## Local setup

Requires Python 3.12 specifically — not the newest Python on your machine. Check available versions with `py -0p` (Windows) and use `py -3.12`.

```bash
python -m venv .venv

# Windows
.venv\Scripts\pip install -e ".[dev]"
copy .env.example .env

# Linux/Mac
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
```

Start the local service stack (Postgres, Redis, ElasticMQ):

```bash
docker compose up -d
```

> **Port note:** Postgres is mapped to **host port 5433**, not 5432 — if your machine already has a native Postgres install on 5432, this avoids the conflict. Adjust `docker-compose.yml` and `.env`'s `DATABASE_URL` together if you change this.

Apply the schema (runs `SQL/*.sql` via a single Alembic baseline migration):

```bash
alembic upgrade head
```

Seed dev data (creates a `super_admin` profile you can log in as, without needing a real Cognito account):

```bash
python -m scripts.seed_dev_profile
python -m scripts.seed_test_prs_content   # 2 real GAD-7 questions, for testing the PRS scoring flow only
```

Run the API:

```bash
uvicorn app.main:app --reload
```

Get a dev token and call an endpoint:

```bash
curl -X POST http://localhost:8000/api/v1/auth/local-login \
  -H "Content-Type: application/json" \
  -d '{"cognito_sub": "dev-super-admin"}'
# use the returned access_token as: -H "Authorization: Bearer <token>"
```

`/api/v1/auth/local-login` only works when `AUTH_MODE=local` (the `.env.example` default) — it's a dev-only stand-in for Cognito, and is dropped entirely at Stage 13's real AWS cutover.

## Project layout

```
app/
├── main.py              FastAPI app factory, middleware, router mounting
├── config.py             Settings (env-driven via pydantic-settings)
├── core/                 Cross-cutting: db/RLS context, auth, exceptions, permissions, outbox events, SQL helpers
├── modules/<name>/        One folder per bounded context — router.py / service.py / repository.py / schemas.py
├── integrations/          External service clients (S3, Razorpay) — local-mode stubs until Stage 13
└── workers/                Background workers (event_relay.py drains the outbox → notifications/SSE)
```

Every module follows the same internal shape: `router.py` (HTTP layer only) → `service.py` (business rules, the only place cross-module calls happen) → `repository.py` (parameterized SQL, no business logic) → `schemas.py` (Pydantic request/response DTOs — never expose ORM/raw dict shapes from a repository directly through the API).

## Two ID concepts worth knowing before writing new code

This schema has `patients.patient_id` (the public API identifier) and `profiles.id` (what most `patient_id`-named foreign key columns actually reference). The API accepts `patients.patient_id` everywhere for consistency; each service resolves `profiles.id` internally via `PatientRepository.get()`. The same pattern applies to doctors (`doctors.doctor_id` vs `doctors.profile_id`) and clinical assistants (`clinical_assistants.ca_id` vs `profiles.id`). Get this backwards and you'll hit a `ForeignKeyViolationError` — see any existing module's `service.py` for the resolution pattern.

## Testing

```bash
pytest
ruff check .
mypy app
```

## Resetting the local database

```bash
docker compose down -v   # -v deletes the volume — full reset
docker compose up -d
alembic upgrade head
python -m scripts.seed_dev_profile
```
