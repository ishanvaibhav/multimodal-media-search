# API reference

Base URL: `http://localhost:8000` · Interactive docs: `/docs`.

All errors share one shape:

```json
{ "error": { "code": "not_found", "message": "human-readable", "detail": null } }
```

**Authentication**: destructive endpoints require `ADMIN_TOKEN` via
`X-Admin-Token` or `Authorization: Bearer <token>`. In production they fail
closed when no token is configured (503) or on a bad token (401). In
development they are open unless a token is set.

---

## Uploads (resumable / chunked)

- `POST /api/uploads/init` — `{filename, file_size, content_type?, chunk_size?}` → `{upload_id, total_chunks, chunk_size, …}`. Validates extension, size (≤ `MAX_UPLOAD_SIZE_GB`), concurrent-upload limit, free disk.
- `POST /api/uploads/{id}/chunk?index=N` — raw octet-stream chunk, streamed to disk + hashed.
- `POST /api/uploads/{id}/complete` — verifies chunks + bytes, reassembles, **ffprobe-validates** the media, queues an indexing job. → `{upload_id, video_id, job_id, status}`.
- `GET /api/uploads/{id}/status` — progress / resume state.
- `DELETE /api/uploads/{id}` — abort + remove chunks.

---

## Media

- `GET /api/media` — params: `search`, `status`, `date_from`, `date_to`, `sort_by`, `sort_order`, `page`, `page_size`, `media_types` (csv), `min_duration`, `max_duration`. `total` honours **exactly** the same filters as the rows.
- `GET /api/media/{id}` — detail + `frames[]` + latest `job`.
- `DELETE /api/media/{id}` — coordinated delete (cancel + wait active job) → removes vectors, frame rows + files, thumbnail, video file, record. Idempotent-safe.
- `POST /api/media/{id}/reindex` — coordinated reindex → new job.
- `GET /api/media/{id}/thumbnail` · `GET /api/media/{id}/frames/{frame_id}` — images (paths resolved + containment-checked).
- `GET /api/media/{id}/stream` — HTTP **Range** streaming (`206`, `Accept-Ranges`).

---

## Search

`POST /api/search`

```json
{
  "query": "person wearing a black shirt",
  "mode": "accurate",               // fast | accurate | metadata
  "video_ids": [],
  "date_from": "2026-08-01", "date_to": "2026-08-17",
  "min_similarity": 0.2, "top_k": 50, "final_results": 5,
  "fine_search": true, "temporal_grouping": true,
  "sort_by": "relevance", "sort_order": "desc",
  "media_types": ["mp4"], "min_duration": 0, "max_duration": 3600,
  "status": "ready"
}
```

Response adds: `mode`, `semantic_search` (bool), `rerank`
(`applied|skipped|unavailable`), and per-result `similarity` (normalized) +
`raw_similarity` (cosine) + `context_frames`.

Date-only `date_to` is interpreted as the **whole day** via an exclusive
next-midnight upper bound; filtering happens backend-side.

- `GET /api/search/history` · `DELETE /api/search/history` — history includes `mode` and `latency_ms`.
- `POST /api/search/feedback` — `{query, relevant, video_id?, frame_id?, timestamp?}` (analytics only).
- `GET /api/search/feedback` — summary.

---

## Jobs

- `GET /api/jobs?limit=&status=` · `GET /api/jobs/{id}` — includes `progress`, `current_stage`, `frames_sampled/kept/embedded`, `frames_total`, `error`.
- `POST /api/jobs/{id}/cancel` — state-machine transition (`QUEUED→CANCELLED`, `RUNNING→CANCELLING`); the worker stops at the next stage boundary and rolls back the partial index.

---

## System

- `GET /api/health` — full component status + `details` (incl. `semantic_search`, `model`, `embedding_dim`, `vectors`).
- `GET /api/health/live` — liveness.
- `GET /api/health/ready` — readiness (in production requires a semantic model; returns 503 otherwise).
- `GET /api/system/info` — versions, embedding, resources, `admin_auth`.
- `GET /api/system/storage` — per-directory usage.
- `GET /api/system/metrics` — counters / gauges / latency histograms.
- `GET /api/system/consistency` — cross-domain drift report (incl. `reconciliation_required`); `?repair=true` (admin) applies safe repairs.

> In **production**, the diagnostic endpoints above require admin
> authentication; the public health endpoints remain the only unauthenticated
> status surface and expose minimal, non-sensitive information (no absolute
> paths/hosts/secrets).

## Admin

- `DELETE /api/admin/data` — **auth required**. Body `{ "confirmation": "DELETE ALL" }`. Runs inside the global maintenance barrier (cancels + waits for workers, wipes Chroma/media/frames/DB, validates clean state). Idempotent — re-running on an empty system succeeds.
- `POST /api/admin/maintenance/start` / `stop` — toggle global maintenance.
- `GET /api/admin/maintenance` — current maintenance state.

## Media streaming (`GET /api/media/{id}/stream`)

Single-range HTTP semantics:

| request | response |
|---------|----------|
| no `Range` | `200`, full file |
| `bytes=0-99` | `206`, `Content-Range: bytes 0-99/<size>` |
| `bytes=500-` | `206` (open-ended) |
| `bytes=-100` | `206` (last 100 bytes) |
| malformed / unsatisfiable (`bytes=100-50`, `bytes=999999999999-`, `bytes=abc`, `bytes=-0`, multi-range) | `416`, `Content-Range: bytes */<size>` |
