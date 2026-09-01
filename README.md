# AI Media Hub

Production-grade **AI media intelligence & search platform**: authorized users
upload images/videos, a background pipeline indexes them with SigLIP
embeddings, and everyone searches the library in natural language —
*"person wearing a black shirt near a car"* → exact timestamps,
representative frames, playable context, exportable evidence.

> **Status — rebuild in progress.** This repository is being rebuilt from
> scratch following `docs/` *Architecture Freeze v1*. The legacy reference UI
> in `frontend/` remains until its replacement lands
> (see `docs/FRONTEND_MIGRATION.md`).

## What works today (Phase 0–2 ✅)

| Area | Status |
|---|---|
| Architecture / docs set (`docs/ARCHITECTURE.md`, `RBAC`, `API`, `DATABASE`, `SECURITY`, `TESTING`) | ✅ Frozen v1 |
| Backend foundation — FastAPI factory, typed config, structured logs, request IDs, response envelope, health endpoints | ✅ |
| Database — SQLAlchemy 2.x schema (16 tables) + Alembic migrations | ✅ |
| Authentication — Firebase token verification, dev mode, bootstrap/provisioning flow | ✅ |
| RBAC — permission layer + admin user management + audit logging | ✅ |
| Docker topology — compose for api/postgres/redis/chromadb/minio | ✅ config |
| Media library, uploads, indexing, search, contexts, jobs, admin ops | 🔜 Phases 3–14 |

## Quick start (local, no credentials needed)

```bash
# 1. Backend
cd backend
python3 -m venv ../.venv && ../.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env                      # AUTH_MODE=dev by default
../.venv/bin/alembic upgrade head
../.venv/bin/uvicorn app.main:app --reload --port 8000

# 2. Smoke it — dev-mode bearer token (identity only; role lives in the DB)
curl -s localhost:8000/api/auth/me -H 'Authorization: Bearer dev:admin@example.com'
# → first user ever becomes ADMIN (see docs/RBAC.md)

# 3. Tests
../.venv/bin/pytest
```

The full container topology (`docker compose up`) additionally starts
PostgreSQL, Redis, ChromaDB and MinIO per `docker-compose.yml`.

### Real Firebase auth

Set in `backend/.env`:

```env
AUTH_MODE=firebase
FIREBASE_PROJECT_ID=<your-project-id>     # firebase-applet-config.json → projectId
```

No service account is needed to *verify* tokens. User creation is done by an
admin through `POST /api/admin/users` (email pre-provisioning) — see
`docs/RBAC.md`.

## Documentation

| Doc | Content |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System shape, boundaries, decisions |
| [docs/RBAC.md](docs/RBAC.md) | Roles, permission matrix, bootstrap |
| [docs/API.md](docs/API.md) | Envelope contract + endpoint catalog |
| [docs/DATABASE.md](docs/DATABASE.md) | Schema & state machines |
| [docs/SECURITY.md](docs/SECURITY.md) | AuthN/Z, upload security, secrets |
| [docs/TESTING.md](docs/TESTING.md) | Levels, coverage, conventions |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Runbooks, env reference, roadmap |
| [docs/FRONTEND_MIGRATION.md](docs/FRONTEND_MIGRATION.md) | UI rebuild plan |

## Engineering rules (non-negotiable)

1. Frontend hiding is never security — the backend authorizes everything.
2. Media bytes never traverse Next.js.
3. No CPU-heavy work inside API request handlers; long work = recoverable jobs.
4. Destructive operations are idempotent/transactional and audited.
5. Embedding model versions are stamped, validated, and never silently mixed.
6. Every feature ships with automated tests.
