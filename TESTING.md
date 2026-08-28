# Testing

## Backend

One canonical reproducible setup:

```powershell
cd "...\media-search\backend"
pip install -r requirements.txt
pip install -r requirements-dev.txt     # pytest, pytest-asyncio, httpx
pip install -r requirements-ml.txt      # SigLIP
pytest -q                               # full suite (deterministic embedder)
EMBEDDING_BACKEND=siglip pytest -q      # real SigLIP
python -m compileall app                # syntax check
```

Tests are fully isolated: each run uses temporary SQLite / Chroma / media /
frame directories and never touches repository runtime `data/`.

### Test files

| file | coverage |
|------|----------|
| `test_unit.py` | sanitization, formatting, temporal grouping (incl. cross-video), dedup hashes, upload validation, date-range repository |
| `test_api.py` | full lifecycle: health → chunked upload → index → search → history → delete; date filtering; admin clear confirmation; abort; jobs listing |
| `test_hardening.py` | date-only exclusive bounds, pagination totals, dedup methods, job state machine, path/id validation, score normalization, deterministic fine-frame ids |
| `test_security.py` | admin auth (dev open / token / production fail-closed), oversized uploads, invalid ids, chunk bounds, escaped-path rejection, search input bounds |
| `test_concurrency.py` | two simultaneous indexing jobs (state isolation), delete-while-indexing (no resurrection), repeated fine search idempotency |
| `test_consistency.py` | orphan jobs, missing files, missing/orphan vectors, safe repair, interrupted-job recovery, relative-path migration |
| `test_p0_hardening.py` | global delete-all (queued/running jobs, idempotent, no resurrection), maintenance barrier, concurrent/repeat upload completion, concurrent + conflicting chunk uploads, upload-concurrency limit |
| `test_range_and_model.py` | HTTP Range (200/206/416/suffix/open-ended/malformed/multi-range), embedding model mismatch fail-closed + dev override + match |
| `test_p1_features.py` | fine-search global-frame & distinct-video budgets, metadata filter parity, datetime normalization (offsets, naive rejection), query length limit, explicit `frame_type`, fine-cache interval roundtrip, DB migration to latest schema |
<<<<<<< HEAD
| `test_context.py` | context formatting (plain text), query normalization/expansion (+ regression: single-component unchanged, connector split, word-minimum, disable, full-query boost), query-embedding cache, deterministic rerank (final_score + diversity), representative context-frame selection (dedup/order/matched-frame), saved-context CRUD + export (txt/json/csv, frames+reason roundtrip), search-result context fields |
=======
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
| `test_fine_cache.py` | interval coverage (disjoint/adjacent/overlapping/partial/complete), config/version invalidation, concurrent extraction (single extraction), maintenance blocks cache writes |
| `test_async_loop.py` | DELETE ALL never blocks the event loop (heartbeat regression), worker `wait_until_idle` timeout, maintenance retained on failed cleanup |
| `test_image_dataset.py` (`-m ml`) | real SigLIP semantic ranking on `test_images/` (dog/cat/car/person) |
| `tests/evaluation/test_temporal_grouping_accuracy.py` | synthetic grouping accuracy (same/distant video, duplicates, missing, out-of-order, boundaries) |
| `tests/evaluation/test_search_accuracy.py` (`-m ml`) | golden-dataset regression: Recall@K / MRR / temporal accuracy thresholds + result traceability |
| `tests/evaluation/test_fine_search_improvement.py` (`-m ml`) | fine search stays in the correct segment, refines via the fine stage, no cross-video contamination |
| `tests/evaluation/test_scalability.py` | keyset pagination (no duplicates/order/count) + synthetic scale benchmark smoke |

### Search-quality regression gate

```powershell
python -m app.eval run --dataset evaluation/golden_dataset.json \
    --data-dir ../data --embedding-backend siglip \
    --min-recall-1 0.5 --min-recall-5 0.75 --min-mrr 0.6 --min-temporal 0.7
```

exits non-zero if a change regresses search accuracy below thresholds.

### Evaluation & benchmarks

```powershell
python -m app.eval run --dataset evaluation_dataset.example.json --data-dir ../data
python -m app.bench embed --frames 100 1000 10000
python -m app.bench search --queries 20 --data-dir ../data
```

## Frontend

```powershell
cd "...\media-search\frontend"
npm run typecheck
npm run build
```

## Acceptance checklist (manual)

1. Start backend, then frontend; open http://localhost:3000.
2. `python -m app.cli index-images --dir ../test_images` → `search-images "dog"`.
3. Upload an MP4 — observe chunked progress, speed, ETA; pause/resume; hide the
   upload panel (state preserved).
4. Processing panel shows sampled/kept/embedded counters + stage + ETA.
5. Search in Fast / Accurate / Metadata modes; verify temporally grouped
   moments, timestamps, similarity, "View Context".
6. Click a result → video seeks to the exact timestamp; copy-timestamp works.
7. Apply a date range / quick filter; confirm backend-side filtering.
8. Hide / Show / Clear Results (index intact); Clear Search; repeat a history
   entry; send relevance feedback.
9. Delete a video → video + frames + metadata + vectors all removed.
10. `GET /api/health`, `/api/health/ready`, `/api/health/live`, `/api/system/consistency`.
11. Production check: `APP_ENV=production` without a semantic model must refuse
    to start; `APP_ENV=production` without `ADMIN_TOKEN` must reject
    `DELETE /api/admin/data`.

## Verified in the reference environment

- Backend: **93 tests pass** (deterministic) incl. concurrency, security,
  consistency, recovery, maintenance/delete-all, upload idempotency, chunk
  concurrency, HTTP Range, model-mismatch; SigLIP semantic test passes with
  the real model.
- Frontend: `tsc --noEmit` clean; `next build` succeeds.
- Live end-to-end: chunked upload → indexing → semantic search → date filter →
  Range streaming → deletion (run against a real server with SigLIP).

## Not exercised in the reference environment

- **Gemini reranking** (needs a `GEMINI_API_KEY`; degrades gracefully).
- **CUDA** (sandbox is CPU-only; `device=cuda` is used automatically when
  available).
- A literal **1 GB upload** (the chunked path is identical; verified with
  multi-chunk uploads).
- `ruff`/`mypy` (not installed); `python -m compileall` is used as the syntax
  gate.
