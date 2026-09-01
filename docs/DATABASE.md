# Database schema — v1 (plan §11–§18)

PostgreSQL in production, SQLite only for local dev/tests. Migrations live in
`backend/alembic/` — `alembic upgrade head` applies them (container
entrypoint does this automatically).

## Tables

| Table | Purpose | Key columns |
|---|---|---|
| `users` | Local identity + role/status (passwords live only in Firebase) | `uid` (binds on first login), `email`, `role`, `status`, `recovery_phone`, `last_login_at` |
| `media` | One row per uploaded asset | `sha256` (dedupe), `storage_key`, `status`, `index_status`, `duration_seconds`, `fps`, `deleted_at` (soft delete) |
| `media_files` | Derivatives (original/preview/proxy) | `kind`, `storage_key`, `sha256` |
| `frames` | Indexed frames; embeddings live in Chroma | `media_id`, `timestamp`, `frame_type`, `phash`, `embedding_id` |
| `jobs` | Every long-running operation (§34) | `type`, `status`, `stage`, `progress`, `counters`, `checkpoint` (crash recovery §37), `idempotency_key` |
| `job_events` | Job lifecycle event log | `event`, `detail` |
| `uploads` | Chunked-upload sessions (§9) | `total_chunks`, `received_chunks`, `sha256`, `expires_at` |
| `upload_chunks` | Received chunk receipts (idempotent retry) | `upload_id+index` unique, `sha256` |
| `search_history` | Per-user query audit (§32) | `query`, `mode`, `filters`, `latency_ms` |
| `search_feedback` | 👍/👎 relevance votes (§33) | `query`, `result_id`, `relevant` |
| `saved_contexts` | Saved evidence moments (§30) | `match_timestamp`, `start/end`, `context_frames`, `score` |
| `fine_search_cache` | Completed fine-search intervals (§22) | `media_id`, interval, `embedding_version` |
| `fine_search_intervals` | In-progress markers (crash recovery) | `job_id`, interval, `status` |
| `model_registry` | Never silently mix models (§18) | `model_name`, `revision`, `dimension`, `preprocessing_version`, `indexing_version`, `active` (unique tuple) |
| `audit_logs` | Immutable admin action log (§42) | `actor_id`, `action`, `target_*`, `request_id` |
| `system_events` | Maintenance/recovery/startup events | `type`, `details` |

## State machines

**Media** (§59): `UPLOADING → REGISTERED → PROCESSING → INDEXED`, plus
`FAILED`, `DELETING → DELETED`, `RECONCILIATION_REQUIRED`.

**Jobs** (§34): `QUEUED → RUNNING → COMPLETED`; `RUNNING → CANCELLING →
CANCELLED`; `RUNNING/CANCELLING → FAILED`; `QUEUED → CANCELLED`. Terminal
states are absorbing. Encoded in `JOB_TRANSITIONS` (`app/db/models.py`) and
covered by tests.

## Conventions

* IDs: 32-char hex (uuid4) generated app-side.
* Enums stored as uppercase strings — human-readable in `psql`, guarded in code.
* Soft delete first (`deleted_at`), async hard purge later (§59).
* All timestamps timezone-aware UTC.
