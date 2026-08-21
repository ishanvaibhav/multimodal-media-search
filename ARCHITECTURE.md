# Architecture

## 1. High-level flow

```
                      USER (browser)
                           │
                           ▼
              ┌──────────────────────────┐
              │  Next.js 14 (TypeScript) │  UI, modes, results, player
              └───────────┬──────────────┘
                          │  chunked upload + search/API (direct, CORS)
                          ▼
              ┌──────────────────────────┐
              │  FastAPI (uvicorn)       │  REST, validation, streaming, auth
              └───────────┬──────────────┘
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
    SQLite           Storage (disk)      Job Worker (in-process)
   (metadata)        data/uploads         data/media
        │                 │                data/frames, thumbnails
        │                 │                        │
        │                 │                        ▼
        │                 │                   FFmpeg/FFprobe
        │                 │                        │
        │                 │                        ▼
        │                 │                   Frame extraction (showinfo PTS)
        │                 │                        │
        │                 │                        ▼
        │                 │                   Dedup (phash/dhash/embedding/none)
        │                 │                        │
        │                 │                        ▼
        │                 │                   SigLIP (batched, CPU/CUDA)
        │                 └───────────────────────►│
        │                                          ▼
        │                                     ChromaDB (persistent)
        │                                          │
        └──────────────────────┬───────────────────┘
                               ▼
                        SEARCH ENGINE
        query → SigLIP text emb → Chroma top-K → per-video temporal grouping
        → bounded fine search → optional rerank → normalization → final events
```

## 2. Key design decisions

### 2.1 Large files never pass through Next.js
The browser performs chunked uploads **directly** against FastAPI. Next.js only
serves the UI and search surface.

### 2.2 Streaming, disk-based processing
Chunks stream to disk in 64 KB pieces with a running SHA-256 (peak memory = one
chunk). FFmpeg reads/writes files on disk; embeddings are batched; vectors are
upserted incrementally.

### 2.3 Real presentation timestamps
Coarse extraction parses `pts_time` from `showinfo`; fine search uses
`-ss … -copyts -i … -vf fps=…,trim=…,showinfo` for fast, accurate windowed
extraction. FPS is treated as an estimate.

### 2.4 Relational + vector split
SQLite is the source of truth for *what exists*; ChromaDB holds embeddings.
`frames` and `videos` persist **relative** paths; a startup migration rewrites
legacy absolute paths. Deletion is coordinated across both domains.

### 2.5 Per-video temporal events
`temporal_group()` keys on `(video_id, timestamp)` — timestamps from different
videos can never merge into one event.

### 2.6 Consistency domains & coordination
SQLite, the filesystem and ChromaDB are separate consistency domains (no
distributed transaction). Operations therefore follow a defined ordering with
compensation and reconciliation:

- **Indexing** persists in ordered stages; a failure/cancel **rolls back** the
  partial index (vectors + frame rows + files).
- **Delete / reindex** first request cancellation of active jobs, wait for a
  terminal state, then hold the per-video lock while cleaning up. The worker
  holds the same lock for the whole pipeline run, so the three operations are
  mutually exclusive.
- A **consistency checker** (`/api/system/consistency`) audits DB↔FS↔Chroma↔jobs
  drift and can apply safe repairs.

### 2.6b Global maintenance barrier (delete-all safety)
`DELETE /api/admin/data` and explicit maintenance mode use a global barrier
with **fully-async quiescence** (no `time.sleep`/blocking waits on the event
loop):

```
ENTER maintenance (gate.start)
  → JobService + worker loop + upload init + fine search all check the gate
  → cancel queued/running jobs
  → AWAIT worker.wait_until_idle(timeout)   (async; loop stays responsive)
  → AWAIT fine-search extraction quiescence (async polling of fine_active)
  → wipe Chroma + media/frames/cache + DB rows (off the loop via to_thread)
  → validate clean state
EXIT maintenance only on success; on failure the system REMAINS in maintenance
```

**Worker quiescence contract**: "idle" means `running_count == 0` — each job
thread (including its FFmpeg subprocesses and Chroma writes) fully finishes
before the count decrements, and the loop cannot pick up new work while the
gate is active. The fine-search layer tracks `fine_active` and blocks new
extractions under maintenance.

### 2.6c Fine-cache interval model
One manifest row per **complete extraction interval** (video_id, interval_ms,
[start, end], extraction_version). Coverage is computed from the interval set
via `compute_gaps()` — disjoint intervals are never merged into a false
continuous range. A complete interval is committed atomically only after all
its frames are persisted; partial extractions leave no row and are
regenerated. Per-(video, interval) locks serialise concurrent extractions with
a cache re-check inside the lock.

### 2.6d Explicit versioning
`app/versioning.py` holds `SAMPLING_VERSION`, `DEDUP_VERSION`,
`PREPROCESSING_VERSION`, `EMBEDDING_VERSION`, `INDEXING_VERSION` and
`FINE_EXTRACTION_VERSION`. The model registry (`model_info` table) records the
active configuration at startup. Vector metadata and fine-cache manifests
carry these versions; any change that affects vector compatibility or indexed
frame semantics invalidates caches / triggers the mismatch check.

### 2.7 Job state machine
```
QUEUED → RUNNING → COMPLETED
              └→ FAILED
              └→ CANCELLING → CANCELLED
QUEUED → CANCELLED   QUEUED → FAILED
```
All transitions go through `JobRepository.transition()` (CAS on the current
status); invalid transitions are rejected.

### 2.8 Scalability seams & process-local state classification

Business logic depends on protocols, not concrete infrastructure:

* `StorageBackend` (storage_backend.py) — local disk today; S3/GCS/Azure later.
* `VectorStoreBackend` (vector_backend.py) — ChromaDB today; Qdrant/Weaviate/pgvector later.
* `JobQueue` (queue.py) — SQLite-backed JobRepository today; Redis/Celery/RQ/Arq/Kafka later.
* Repository layer over SQLite today; PostgreSQL later (keyset pagination already available for large lists).

Process-local state is classified as:

| State | Class | Notes |
|-------|-------|-------|
| per-video locks, chunk locks, fine-cache locks | **A — intentionally process-local** | safe under the single-process model; move to distributed locks only with a distributed queue |
| MaintenanceGate flag | **A** | single-process barrier; a distributed deployment needs a shared flag |
| UploadLimiter semaphore | **A** | advisory; DB count remains the durable gauge |
| worker `_running_count`, embedding cache, metrics | **A** | documented process-local; metrics need an external collector at scale |
| jobs / uploads / videos / frames / fine-cache manifests / model registry | **B — persisted** | SQLite (WAL) + Chroma on disk |
| future distributed queue + locks | **C — must become distributed** | only when multi-process is adopted; not required now |

## 3. Backend module map

```
backend/
  app/
    main.py              app, CORS, request-id middleware, lifespan + recovery
    config.py            pydantic-settings (all env config)
    container.py         composition root
    logging_config.py    structured logging
    exceptions.py        AppError hierarchy
    utils.py             sanitize / validate / date-range helpers
    domain/models.py     enums + dataclasses
    api/                 uploads media search jobs admin health system deps
    application/         upload/indexing/search/media/job services
                         recovery_service.py  consistency_service.py
    infrastructure/      database(storage+migrations) storage(vector+relative paths)
                         ffmpeg embedding vectorstore reranker perceptual metrics
                         coordinator.py (per-video locks) repositories/
    workers/indexing_worker.py
    schemas/
    cli.py  eval.py  bench.py
  tests/                 unit / api / image_dataset / hardening / security /
                         concurrency / consistency
```

## 4. Frontend module map

```
frontend/
  app/ layout.tsx page.tsx globals.css
  components/ Header (health/degradation/consistency/admin token)
              upload/UploadPanel  search/SearchPanel (modes, filters, history)
              results/ResultsPanel (copy ts, feedback, rerank note)
              media/MediaLibrary  player/VideoPlayer  jobs/JobsPanel (counters+ETA)
              ui/ (primitives, modal, confirm)
  lib/ api.ts uploads.ts format.ts types.ts
  hooks/ useUploads useMedia useJobs useSearch usePolling
```

## 5. Data layout

```
data/
  uploads/     in-flight chunk dirs
  media/       {video_id}.{ext}
  frames/      {video_id}/frame_%06d.jpg (+ fine frames: {video_id}_fine_{ms}_{ts}.jpg)
  thumbnails/  {video_id}.jpg
  chroma/      persistent ChromaDB
  database/    app.db (SQLite, WAL, PRAGMA user_version migrations)
  logs/  temp/
```

## 6. Model traceability

Every Chroma vector is stamped with `embedding_model`, `model_version`,
`embedding_dim`, `preprocessing_version`, `indexing_version`. The vector store
fails if the configured embedding dimension differs from existing vectors, and
warns if the model changed (recommending a reindex).

## 7. Failure & recovery

- Interrupted jobs (crash) → failed + partial-index rollback (or requeue).
- Abandoned uploads older than `MAX_UPLOAD_AGE_HOURS` → cleaned.
- Stale temp files → cleaned; stale fine-cache manifests → cleaned.
- Search-history retention enforced at startup.
- Legacy absolute paths → rewritten to relative on startup.
- If rollback itself fails, the video is flagged `needs_reconciliation` (never
  hidden) and reported by the consistency checker.
- Readiness (`/api/health/ready`) reports 503 in production without a semantic
  model, on model mismatch, or during maintenance.
