# Lio Procurement App

Upload vendor-offer PDFs, extract the details with AI, and track procurement
requests across your organization.

- **`frontend/`** — Next.js 15, React 19, Tailwind CSS 4
- **`backend/`** — FastAPI, SQLAlchemy, Alembic, JWT auth
- **Database** — PostgreSQL 16

## Prerequisites

- **Docker Desktop** (or Docker Engine) with **Docker Compose v2** — running before you start.
- An **OpenAI API key** is optional: PDF extraction and AI catalog matching need one; the rest of the app, including direct catalog-name matching, works without it.

## Quick start (Docker)

```bash
cp .env.example .env      # add OPENAI_API_KEY for extraction and AI catalog matching
docker compose up --build
```

| | URL |
| --- | --- |
| App | http://localhost:3200 |
| API + docs | http://localhost:7200 · http://localhost:7200/docs |

Sign in with a demo account — no registration needed (password `demo1234`).
Development happens in the **Acme Corp** organization, which has one of each role:

| Account | Role | Can do |
| --- | --- | --- |
| `admin@acme.com` | admin | everything incl. members & settings |
| `buyer@acme.com` | buyer | approve requests |
| `requester@acme.com` | requester | create & track requests |

Acme requests below **$2,000**, including tax and shipping, are approved automatically.
At $2,000 and above, a buyer or admin approves them in the **Approvals** tab.
Admins can change the limit in Organization → Settings; other tenants have no limit by default.

Other tenants exist to show the multi-tenant setup — **Walmart, Schaeffler,
Siemens, BMW** — each with an admin at `admin@<company>.com` (same password).
Their data is fully isolated from Acme's.

On startup the backend applies schema migrations and seeds the database (demo
accounts, organizations, commodity groups, suppliers, articles and Acme settings) — both steps are
idempotent, so restarts never duplicate or overwrite anything (your data lives
in the `postgres_data` volume; `docker compose down -v` resets it). The OpenAI
key enables PDF extraction and AI catalog matching.

## Local development

```bash
docker compose up db        # Postgres only

# backend  → http://localhost:7200
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .
export CORS_ORIGINS=http://localhost:3200
# Optional: export OPENAI_API_KEY in this shell; Compose reads the root .env itself.
alembic upgrade head        # schema
python -m app.seed          # reference data (idempotent)
uvicorn app.main:app --reload --port 7200

# frontend → http://localhost:3200 (another terminal, from the repository root)
cd frontend
npm install
npm run dev
```

## Tests

Install test tools in the backend environment with `python -m pip install pytest httpx`.

```bash
(cd backend && python -m pytest)    # offline tests (in-memory SQLite)
(cd frontend && npx tsc --noEmit && npm run build)
```

The committed backend suite has 74 tests. Quote fixtures and live scorecards are
local case-study material and are not included; live tests are excluded by default.

## Layout

```
backend/    FastAPI app — routers, services, models, schemas, core; Alembic migrations
frontend/   Next.js app — app-router pages, typed API client (src/lib/api), components
docker-compose.yml
```
