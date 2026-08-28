# AI Media Search — Temporal Multimodal Video Search

A production-hardened, fully local platform for searching large videos with
natural language. Upload a video (1 GB and beyond, resumably, in chunks), let
the background worker index it, then ask *"person wearing a black shirt"* and
get back **exact timestamps**, representative frames and one-click playback.

```
USER UPLOADS A LARGE VIDEO
        ↓  resumable chunked upload (direct to FastAPI, never through Next.js)
VIDEO STORED ON DISK
        ↓  FFprobe metadata
COARSE FRAME EXTRACTION (1 frame / N seconds)
        ↓  perceptual deduplication (phash | dhash | embedding | none)
BATCHED SIGLIP IMAGE EMBEDDINGS
        ↓  ChromaDB vector index (model + dimension stamped per vector)
USER TEXT QUERY → SigLIP text embedding → semantic retrieval
        ↓  per-video temporal grouping → bounded cache-aware fine search
        ↓  (optional Gemini rerank) → score normalization
FINAL VIDEO MOMENTS → frames + timestamps + playback
```

---

## 1. Stack

| Layer | Technology |
|-------|------------|
| Frontend | Next.js 14 (App Router) · TypeScript (strict) · React 18 · Tailwind CSS |
| Backend | FastAPI · Pydantic v2 · SQLite (WAL + migrations) · ChromaDB (persistent) |
| Media | FFmpeg / FFprobe (auto-discovered, `imageio-ffmpeg` fallback) |
| ML | SigLIP (`google/siglip-base-patch16-224`) via Hugging Face Transformers · PyTorch (CPU/CUDA) |
| Jobs | In-process background worker (bounded concurrency, state machine, cooperative cancel) |

---

## 2. Requirements

- **Python 3.10+** (tested on 3.13) · **Node.js 18+** (tested on 20)
- **FFmpeg + FFprobe** — or nothing: the binary bundled with `imageio-ffmpeg`
  is auto-discovered on Windows.
- **Real SigLIP embeddings**: `pip install -r requirements-ml.txt` (weights are
  fetched once from the Hugging Face Hub and cached).

### Semantic-search policy (explicit, never silent)

`EMBEDDING_BACKEND`:

| value | behaviour |
|-------|-----------|
| `siglip` | require the real model (fail fast if unavailable) |
| `auto` (default) | SigLIP when available; deterministic fallback **only outside production** |
| `deterministic` | explicit non-semantic baseline (CI/plumbing only) |

In **production** (`APP_ENV=production`) a missing semantic model **aborts
startup** — degraded search is never presented silently. The deterministic
fallback exists only so tests/CI can run without a model download. Health and
search responses always expose `semantic_search: true|false`.

**Model compatibility**: the vector store validates dimension, model name,
model revision, preprocessing version and indexing version against the
existing index. Any mismatch **fails closed** (startup aborts) unless
`ALLOW_MODEL_MISMATCH=true` is explicitly set (dev only, loud warning +
`model_mismatch` exposed in health). Two different models with the same
dimension are never silently mixed.

---

## 3. Installation

### Backend (PowerShell)

```powershell
conda create -n multimodal-search python=3.11 -y
conda activate multimodal-search
cd "...\media-search\backend"
pip install -r requirements.txt
pip install -r requirements-ml.txt
```

### FFmpeg

Windows: `pip install imageio-ffmpeg` (already in `requirements.txt`) — nothing
else to do. Or set `FFMPEG_PATH`/`FFPROBE_PATH` to a static build. Linux:
`sudo apt install ffmpeg`.

### Frontend

```powershell
cd "...\media-search\frontend"
npm install
```

---

## 4. Running

**Terminal 1 (backend):**

```powershell
conda activate multimodal-search
cd "...\media-search\backend"
python -m uvicorn app.main:app --reload
```

**Terminal 2 (frontend):**

```powershell
cd "...\media-search\frontend"
npm install
npm run dev
```

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| Backend | http://localhost:8000 |
| Swagger | http://localhost:8000/docs |

On startup the backend validates Python/FFmpeg/SQLite/ChromaDB/model/storage,
runs crash recovery, and rewrites any machine-specific absolute paths into
portable relative paths.

---

## 5. Quick start (image dataset)

```powershell
cd "...\media-search\backend"
python -m app.cli index-images --dir ../test_images
python -m app.cli search-images "dog"
```

Generate a synthetic video: `python -m app.cli make-test-video --out ../demo/sample.mp4 --seconds 60`

---

## 6. Testing / validation

One canonical, reproducible setup:

```powershell
cd "...\media-search\backend"
pip install -r requirements.txt
pip install -r requirements-dev.txt     # pytest, pytest-asyncio, httpx
pip install -r requirements-ml.txt      # SigLIP (torch + transformers)
pytest -q                               # full suite (isolated temp dirs)
EMBEDDING_BACKEND=siglip pytest -q      # adds the real SigLIP semantic test
python -m compileall app                # syntax check
```

Tests never touch runtime `data/` — they use isolated temporary SQLite/Chroma/
media/frame directories per test run.

```powershell
cd "...\media-search\frontend"
npm run typecheck
npm run build
```

Evaluation & benchmarking: `python -m app.eval run --dataset evaluation_dataset.example.json` ·
`python -m app.bench embed --frames 100 1000 10000`. See [TESTING.md](TESTING.md).

---

## 7. Key architecture points

### Large files never pass through Next.js
The browser performs chunked uploads **directly** against FastAPI
(`/api/uploads/*`). A 1 GB file becomes ~103 × 10 MB chunks, streamed to disk
with SHA-256, resumable (status → skip received chunks), retryable, pausable,
cancellable. Reassembly validates chunk count + byte total and **probes the
actual media** before registering the video.

### Portability & safety
- Persisted paths are **relative to DATA_DIR** (`media/<id>.mp4`,
  `frames/<id>/…`); a startup migration rewrites legacy absolute paths.
- All filesystem access resolves through `StorageService` with traversal +
  symlink-escape guards; serving verifies containment in the media/frames root.
- IDs are validated (`^[A-Za-z0-9._-]+$`) before touching the filesystem.

### Concurrency & lifecycle
- Indexing state is **job-scoped** (no shared mutable pipeline state) — two
  jobs can run safely.
- A **per-video lock + cancellation protocol** makes INDEX / REINDEX / DELETE
  mutually exclusive: delete cancels + waits for active jobs, holds the lock,
  then cleans up — a deleted video can never reappear.
- Jobs follow an explicit **state machine** (`QUEUED → RUNNING → COMPLETED /
  FAILED / CANCELLING → CANCELLED`); invalid transitions are rejected.

### Crash recovery
On restart: interrupted jobs are failed and their **partial index rolled
back** (or re-queued with `AUTO_REQUEUE_ON_RESTART=true`), stale temp files
and abandoned uploads are cleaned, and paths are normalized.

### Storage consistency
`GET /api/system/consistency` reports missing files, orphan files, missing /
orphan vectors, orphan jobs and reconciliation-required videos across
SQLite ↔ filesystem ↔ ChromaDB; `?repair=true` (admin) applies safe fixes only.

### Global maintenance (DELETE ALL safety)
`DELETE /api/admin/data` runs inside a **global maintenance barrier** using
fully-async quiescence (never blocking the event loop):

```
normal → maintenance → cancel jobs → AWAIT worker idle →
AWAIT fine-search quiescence → wipe → validate → normal
```

New indexing jobs, uploads **and fine-search cache writes** are rejected while
maintenance is active. Workers must actually terminate (job threads, FFmpeg
subprocesses and Chroma writes all complete before the worker reports idle);
fine-search extractions must quiesce before the wipe. If quiescence times out,
DELETE ALL fails **without reporting success** and the system **remains in
maintenance** so the operation can be retried safely. A worker can never
recreate frames/vectors after the wipe. Maintenance is also toggleable via
`POST /api/admin/maintenance/start|stop`, visible in `/api/health`
(`maintenance`), and `/api/health/ready` reports **not ready** during it.

### Fine-search cache (interval-based, atomic, concurrency-safe)
The cache manifest stores **one row per complete extraction interval**
(`[start, end]`, interval, extraction version). Coverage is computed from the
actual interval set — disjoint windows like `[0,5]` + `[10,15]` correctly
report a missing gap `[5,10]`, which is then extracted (only the gap, never
regenerating everything). A complete interval is committed **only after** every
frame of that window is persisted and validated, so a partial extraction can
never masquerade as complete. Per-`(video, interval)` locking with a cache
re-check inside the lock prevents two concurrent searches from extracting the
same window. Config changes (interval, extraction version, preprocessing
version) invalidate reuse.

### Upload completion & chunk idempotency
Completion uses an atomic CAS transition (`uploading → completing →
completed`): concurrent/repeated completion requests produce **exactly one**
video and one job, and replays return the stored result. Chunk writes are
serialised per `(upload, index)`, written to a unique temp file, hashed, and
atomically renamed — re-uploading identical bytes succeeds, different bytes
yield `409 Conflict`. The concurrent-upload limit is enforced by a
race-safe semaphore (`MAX_CONCURRENT_UPLOADS`).

### Fine-grained search
Bounded (`FINE_SEARCH_MAX_*`), **cache-aware** (deterministic frame ids +
DB upsert — repeated identical searches reuse artifacts, never duplicate),
and preserves temporal context (neighbouring frames).

### Observability
Request-id middleware, structured logs, `/api/health`, `/api/health/live`,
`/api/health/ready`, and `GET /api/system/metrics` (counters + latency
histograms).

---

## 8. Security

- **Admin auth**: destructive endpoints (`/api/admin/*`,
  `GET /api/system/consistency?repair=true`) require `ADMIN_TOKEN`
  (`X-Admin-Token` or `Authorization: Bearer`). In production the operation
  **fails closed** if no token is configured; in development it is open unless
  a token is set.
- Uploads: extension + size + free-disk validation, real ffprobe media
  validation, chunk-size/byte verification.
- Gemini rerank: optional, timeout + candidate limits, output validated as a
  permutation, labels sanitized/truncated. Search works without it.

---

## 9a. Temporal context & the Context Viewer

Every video result carries a window-bounded **context segment**
(`context_start → context_end`, default ±8 s around the match), the nearby
frames within it, a grounded **reason** string (matched frame, raw cosine,
frame count), and a clean plain-text `context_text` block — the reason never
invents content. The **Context Viewer** (a result's *View Context*) supports
**Copy Context / Copy Timestamp / Copy All / Save / Download
(TXT·JSON·CSV)** with "Copied!" feedback, and the **Saved Contexts** panel
keeps saved results with per-item copy/download/remove and full export.

## 9b. Query understanding & deterministic reranking (incremental, measured)

- **Query expansion**: connector queries ("a dog near a red car") are split
  into components and fused (`QUERY_EXPANSION`, `FUSION_METHOD=max|sum`). The
  **full query stays dominant** via `FUSION_FULL_QUERY_BOOST` — components add
  recall, the full query keeps precision. Single-component queries are
  byte-for-byte unchanged, so the measured baseline (Recall@1/5/10 = 1.00) is
  preserved — regression-tested.
- **Deterministic reranking** (`RERANK_DETERMINISTIC`): a configurable weighted
  score over documented signals — semantic (normalized vector score),
  **full-query relevance** (bonus when the representative matched the full
  query embedding, keeping the original query dominant over expansion
  components), neighbor (temporal coherence), diversity (video spread), and a
  duplicate penalty (off by default; temporal grouping already merges
  near-duplicates). Defaults are conservative and preserve single-video,
  single-component ordering.

The context block now separates **RETRIEVED EVIDENCE** (deterministic facts +
representative frame timestamps) from an optional **AI SUMMARY** (grounded,
Gemini-only, never hallucinated) — see `context_text`.
- **Temporal context**: context frames are selected by deduplicating near
  neighbours (perceptual hash), always keeping the matched frame, and picking
  representative frames spread across the interval; the context segment is
  derived from the actual selected evidence. An optional **grounded AI
  summary** (`LLM_CONTEXT_SUMMARY` + Gemini) is appended when available and
  never fails context generation otherwise.

## 9. Search modes

| mode | pipeline |
|------|----------|
| `fast` | vector retrieval → temporal grouping |
| `accurate` (default) | + bounded fine search → optional rerank → normalization |
| `metadata` | filename/metadata match against the library |

Filters: video, date range (exclusive-upper-bound day handling), media type,
duration, indexed status, min similarity, top-K, sort. Relevance feedback
(thumbs up/down) is stored for analytics/evaluation only.

---

## 10. Environment variables

Full annotated list in [`.env.example`](.env.example). Highlights:
`ADMIN_TOKEN`, `MAX_UPLOAD_SIZE_GB`, `CHUNK_SIZE_MB`, `DEDUP_METHOD`,
`FRAME_INTERVAL_SECONDS`, `FINE_FRAME_INTERVAL_SECONDS`,
`FINE_SEARCH_MAX_*`, `TEMPORAL_GROUP_WINDOW_SECONDS`,
`MAX_RESULTS_PER_EVENT`, `RANKING_NORMALIZATION`, `TOP_K`, `FINAL_RESULTS`,
`EMBEDDING_BACKEND`, `SIGLIP_MODEL`, `HF_TOKEN`, `GEMINI_API_KEY`,
`LLM_RERANK`, `CHROMA_COLLECTION`, `FFMPEG_PATH`.

---

## 11. API summary

Uploads (`init/chunk/complete/status/delete`) · Media
(`list/detail/delete/reindex/thumbnail/frames/stream`) · Search
(`POST /api/search`, `history`, `feedback`) · Jobs (`list/get/cancel`) ·
System (`health`, `health/live`, `health/ready`, `info`, `storage`, `metrics`,
`consistency`) · Admin (`DELETE /api/admin/data` — auth required).

Full reference: [API.md](API.md) · Architecture: [ARCHITECTURE.md](ARCHITECTURE.md) ·
Development: [DEVELOPMENT.md](DEVELOPMENT.md) · Testing: [TESTING.md](TESTING.md).

---

### Deployment model (single-process, explicit)
This revision is **single-process**: one embedded background worker.
`MAX_CONCURRENT_JOBS` bounds concurrent *threads* within that process, but
**multiple backend worker processes are NOT supported** — per-video
coordination and upload/chunk locks are process-local. Do not run
`uvicorn --workers N` with `N > 1`; a future revision can move coordination to
Redis / Celery / RQ. The startup log states the active model explicitly.

---

### Scaling path & deployment model

**Current deployment model**: single process with an embedded worker
(`MAX_CONCURRENT_JOBS` threads). Per-video coordination, chunk locks, upload
limits and the maintenance barrier are **process-local** — multiple backend
worker processes are NOT supported and are warned about at startup.

**How it scales without a rewrite** — business logic already depends on
interfaces, not concrete infrastructure:

| Concern | Interface | Today | Future |
|---------|-----------|-------|--------|
| Object/media storage | `StorageBackend` (`infrastructure/storage_backend.py`) | local disk | S3 / GCS / Azure Blob |
| Vector store | `VectorStoreBackend` (`infrastructure/vector_backend.py`) | ChromaDB | Qdrant / Weaviate / pgvector |
| Job queue | `JobQueue` (`infrastructure/queue.py`) | SQLite (JobRepository) | Redis / Celery / RQ / Arq / Kafka |
| Metadata DB | repository layer | SQLite (documented scale ceiling ~10⁵–10⁶ rows; keyset pagination available) | PostgreSQL |

Indexing is queue-oriented (deterministic ids, state machine, retry count,
checkpoint field, progress, cancellation), media is streamed in bounded
batches, and search operates only on bounded candidate sets (top-K → grouping →
fine search → optional rerank) — so millions of vectors are never loaded into
memory or sent to an LLM.

**Measured synthetic scale** (`python -m app.bench synthetic`, deterministic
embeddings, real SQLite+Chroma code paths): search latency stays ~2–4 ms across
1,000 → 100,000 vectors; 100,000 vectors indexed in ~287 s at ~35 videos/s
(per-video transaction pattern — batch embedding already amortizes the ML cost).

### Search accuracy & evaluation

Search quality is **measured, not assumed**. A versioned golden dataset
(`backend/evaluation/golden_dataset.json`) drives a regression gate:

```powershell
cd "...\media-search\backend"
python -m app.eval run --dataset evaluation/golden_dataset.json --data-dir ../data --embedding-backend siglip
pytest tests/evaluation/ -m ml        # accuracy regression tests (SigLIP)
```

Measured on the golden dataset (8 queries, deterministic demo video, real
SigLIP): **Recall@1/5/10 = 1.00, MRR = 1.00, video recall = 1.00, temporal
accuracy = 1.00**. Every result is fully traceable (`retrieval_stage`,
`frame_id`, `score`, `raw_cosine`, `group_event_index`, `embedding_model`,
`model_version`, `indexing_version`). Fine search is gated by the invariant
that it only replaces a coarse representative when it scores higher — it
narrows or preserves temporal precision, never degrades it.

## 11b. Images and videos — one pipeline, two media types

Uploads accept **images** (JPG/JPEG/PNG/WEBP/GIF) **and** videos (MP4/MOV/AVI/
MKV/WEBM) through the same resumable, chunked upload protocol. They are
validated differently (PIL decode + dimension/pixel limits for images —
including a decompression-bomb guard `MAX_IMAGE_DIMENSION` /
`MAX_IMAGE_PIXELS`; ffprobe for videos) and indexed via two dispatched
pipelines:

- **Image**: validate → SigLIP image embedding (single vector) → Chroma + the
  image stored as its own "frame" (timestamp 0, `media_type=image`).
- **Video**: validate → FFmpeg frame extraction → dedup → batched SigLIP →
  Chroma (`media_type=video`, timestamps preserved).

Search is unified: a text query returns both image results and video-frame
results in the same embedding space, each tagged with `media_type`; a
`media_type` filter (all / images / videos) is available in the UI and API.
Large uploads go **directly to FastAPI** in bounded chunks (chunk size is
server-configurable via `CHUNK_SIZE_MB` and discoverable at
`GET /api/uploads/config`, so the frontend and backend always agree) — a
>10 MB file never becomes a single request, which is why it cannot hit any
10 MB proxy/body limit.

## 12. Known limitations

- **CPU embedding speed** dominates fine search on CPU; CUDA makes it
  near-instant (coarse search is always fast).
- **First-run model download** (~400 MB) is fetched once and cached.
- **Gemini rerank** is implemented but was not exercised against the live API
  in the reference environment (no key); the vector-ranked path is the default.
- **Single-process deployment** only (one embedded worker); multi-process
  coordination is intentionally not claimed.
- In-process metrics are process-local (documented); a Prometheus/OTel path is
  the future scaling direction.
- Fine-search embedding cache is in-memory (per-process), keyed by model
  identity — it never reuses embeddings across incompatible models.

## 13. Scaling path

Interfaces isolate `EmbeddingService`, `VectorStore`, repositories and the
worker, so Redis/Celery, PostgreSQL, S3, Qdrant/Weaviate, GPU workers and
Kubernetes can be adopted without rewriting the application.
