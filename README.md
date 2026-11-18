# Haatly

Private seller-led commerce for local sellers, textile first.

Each seller runs one store, approved by an admin before it can publish. Buyers find stores by PIN
code or distance. Stores have members, products can be members-only, and a store can hide its
prices from non-members end to end.

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 async + asyncpg · PostgreSQL 16 (PostGIS, pg_trgm) ·
TigerBeetle · Jinja + HTMX · Caddy · Docker Compose.

Architecture decisions live in [`docs/adr/`](docs/adr).

## Development (Windows + PowerShell)

Prerequisites: Docker Desktop, and uv (`winget install --id astral-sh.uv -e`).

```powershell
./tasks.ps1 setup      # Python 3.12, dependencies, .env, git hooks
./tasks.ps1 up         # Postgres, TigerBeetle and Caddy in Docker
./tasks.ps1 migrate    # apply migrations as the owner role
./tasks.ps1 run-api    # API on http://127.0.0.1:8000, behind Caddy on https://localhost:8443
./tasks.ps1 run-worker # outbox relay, jobs and scheduler
./tasks.ps1 check      # every gate CI runs, except the Docker smoke boot
./tasks.ps1 down
```

Other tasks: `test`, `lint`, `typecheck`, `migrate-roundtrip`, `smoke`, `hooks`.

## Data attribution

PIN code locations come from the India Post pincode directory published on
[data.gov.in](https://data.gov.in) under the Government Open Data License – India.
