# Architecture — AI Media Hub (Freeze v1)

Status: **Frozen for V1** · Phase 0 deliverable (master plan §66)

## 1. System shape

```text
                ┌─────────────┐
                │   Next.js   │  (Firebase Auth client)
                └──────┬──────┘
                       │ HTTPS + Bearer token
                       ▼
                ┌─────────────┐
                │   FastAPI   │  stateless API (auth, RBAC, media, search)
                └──────┬──────┘
        ┌──────────────┼───────────────┬──────────────┐
        ▼              ▼               ▼              ▼
   PostgreSQL       Redis         Object Storage   ChromaDB
   (system of      (job queue,   (originals,      (frame
    record)         locks)         frames, thumbs)  vectors)
                       │
                       ▼
                 Indexing Workers  (FFmpeg → dedup → SigLIP → Chroma)
```

Hard rules (plan §70):

1. Frontend visibility is **not** security — the backend authorizes every request.
2. Media bytes never traverse Next.js; uploads go browser → FastAPI (or object storage).
3. No CPU-heavy work in API request handlers; every long operation is a **job**.
4. Every job is recoverable (checkpoints); every destructive op is idempotent/transactional.
5. Embedding model, preprocessing version and index version are stamped on every
   vector and fail closed on mismatch.

## 2. Process boundaries

| Process | Responsibility | Scaling |
|---|---|---|
| `api` | HTTP, auth, RBAC, CRUD, search orchestration | horizontal, stateless |
| `worker` | uploads finalization, indexing, fine search, maintenance | horizontal, CPU/GPU |
| `postgres` | relational truth (users, media, jobs, uploads, audit…) | single primary |
| `redis` | job queue, distributed locks, short-lived caches | single node ok |
| `chromadb` | vector similarity search | single node →迁移 Qdrant/pgvector possible |
| `minio` | S3-compatible object storage (prod: S3/R2/GCS) | external |

Local development collapses everything into one process + SQLite, but every
module is written against the boundaries above (plan §66 "biggest change").

## 3. Repository layout

```text
backend/
├── app/
│   ├── api/           # HTTP layer: routers, deps, envelope, middleware
│   ├── auth/          # Firebase verification, permission layer, provisioning
│   ├── core/          # config, logging, audit
│   ├── db/            # SQLAlchemy models, engine/session
│   ├── schemas/       # Pydantic API contracts
│   ├── services/      # domain services (Phases 3+)
│   ├── repositories/  # persistence access (Phases 3+)
│   └── ml/            # embeddings/retrieval/reranking/temporal (Phases 5–8)
├── alembic/           # migrations
├── tests/             # unit + integration (pytest)
└── requirements{,-dev,-ml}.txt

frontend/              # Next.js app (rebuilt in later phases — see docs/FRONTEND_MIGRATION.md)
docs/                  # this documentation set
docker-compose.yml     # dev topology
```

## 4. Data flow (target V1)

Upload: `INIT → CHUNK×N → COMPLETE → VERIFY(sha256) → FFprobe → REGISTER media → INDEX job`.
Indexing: `FFprobe → frames@2s → phash dedup → SigLIP embed → Chroma upsert`.
Search: `query → SigLIP text embed → Chroma top-K → temporal grouping →
[accurate mode: fine extraction + rerank] → hydrated results`.

## 5. Consistency & recovery

Three storage domains (DB / objects / vectors) can disagree; the
**consistency engine** (§38) scans for missing/orphan rows, files and
vectors and repairs only provably-safe cases. **Maintenance mode** (§39)
blocks uploads/indexing/destructive races while an admin performs global
operations. Interrupted jobs resume from `jobs.checkpoint` (§37).

## 6. Versioning contract (§58)

`POST /index` and vector metadata carry `model`, `revision`,
`preprocessing_version`, `indexing_version`. Changing any of them invalidates
old vectors; `/api/system/version` exposes the live values.

## 7. Decision log

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Sync SQLAlchemy 2.x + threadpool | Simplest correct model for CPU-centric workers; revisit if IO-bound profile changes |
| D2 | Permission enum + role mapping module | Plan §7 — no scattered role checks |
| D3 | Token proves identity only; role/status always from DB | Revocation is instant, claims stay out of tokens |
| D4 | Admin pre-provisions by email; uid binds on first login | Server-side user creation needs no service account |
| D5 | Dev auth mode (`dev:email` bearer) | Enablles local/CI flows; startup aborts if combined with `APP_ENV=production` |
| D6 | `create_all` in dev/test, Alembic for prod | DX speed vs. controlled prod migrations; Alembic is authoritative |
| D7 | ChromaDB first | Plan default; interface isolation keeps swap cost low |
