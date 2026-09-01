# Operations (plan §47, §57, draft)

## Run

```text
dev (bare metal)   cd backend && uvicorn app.main:app --reload
dev (containers)   docker compose up           # api + postgres + redis + chromadb + minio
prod               container image backend/Dockerfile; alembic upgrade head in entrypoint
```

## Environment reference

Canonical list with defaults & validation lives in `backend/app/core/config.py`
(copy `backend/.env.example` → `.env`). Highlights:

| Var | Default | Notes |
|---|---|---|
| `APP_ENV` | `development` | `production` triggers the fail-fast safety gate |
| `DATABASE_URL` | sqlite under `backend/data/` | PostgreSQL for prod: `postgresql+psycopg://…` |
| `AUTH_MODE` | `dev` | must be `firebase` in production |
| `FIREBASE_PROJECT_ID` | — | required in production |
| `BOOTSTRAP_ADMIN_EMAIL` | — | pins who claims the first ADMIN seat |
| `STORAGE_BACKEND` | `local` | `s3` + `S3_ENDPOINT/BUCKET/…` for MinIO/R2/S3 |
| `EMBEDDING_BACKEND` | `auto` | §70 rule 8: production requires the real model |
| `MAX_UPLOAD_SIZE_GB` / `UPLOAD_CHUNK_SIZE_MB` | 4 / 8 | enforced in Phase 4 |

## Health & observability

* `GET /health|/health/live|/health/ready` — liveness vs. dependency readiness.
* Every request logged with `X-Request-ID`; send your own to correlate.
* `system_events` table records startup/shutdown/maintenance/recovery.

## Roadmap of runbooks (fill in at their phases)

* Upload janitor (expire stale `uploads` past `MAX_UPLOAD_AGE_HOURS`) — Phase 4
* Job crash recovery & reconciliation — Phase 11
* Consistency scan / safe-repair — Phase 10
* Maintenance-mode barrier protocol (`MAINTENANCE_STARTED/STOPPED`) — Phase 10
* Backups: WAL/object versioning recommendations — Phase 14
