# Development guide

## 1. Project layout

```
media-search/
  backend/            FastAPI + SQLite + ChromaDB + SigLIP + worker
  frontend/           Next.js 14 + TypeScript + Tailwind
  test_images/        dog.jpg, cat.jpg, car.jpg, person.jpg (SigLIP smoke test)
  demo/               (optional) synthetic demo video via `app.cli make-test-video`
  .env.example        backend environment reference
  README.md  ARCHITECTURE.md  API.md  DEVELOPMENT.md  TESTING.md
```

## 2. Environment

- Backend config lives in `backend/.env` (see `.env.example`). Every value has
  a safe default; `pydantic-settings` reads env vars and `.env`.
- Frontend config: `frontend/.env.local` → `NEXT_PUBLIC_API_BASE` (default
  `http://localhost:8000`).

## 3. Windows notes (PowerShell)

- **Quote all paths**, especially ones with spaces/parentheses:
  ```powershell
  cd "C:\Users\You\workspace (1)\media-search\backend"
  ```
  never `cd C:\Users\You\workspace (1)\media-search\backend`.
- Prefer the **bundled FFmpeg** (`pip install imageio-ffmpeg`) or set
  `FFMPEG_PATH`/`FFPROBE_PATH` to an extracted static build.
- `conda activate multimodal-search` before running the backend.
- `python -m uvicorn app.main:app --reload` works on PowerShell as-is.

## 4. Adding an endpoint (checklist)

1. Schema in `backend/app/schemas/…`.
2. Service method in `backend/app/application/…`.
3. Router in `backend/app/api/…` + `include_router` in `main.py`.
4. Typed client function in `frontend/lib/api.ts` + types in `lib/types.ts`.
5. Test in `backend/tests/`.

## 5. Conventions

- **No TODOs / placeholders** in core paths; every button and endpoint is real.
- **No `any`** in the frontend (strict TypeScript).
- Paths are built only through `StorageService`/`ensure_within`; filenames are
  sanitized (`utils.sanitize_filename`).
- Services are wired in `container.py` (poor-man's DI); the API layer only
  touches the container.
- Exceptions extend `AppError` and carry an HTTP status + machine-readable
  `code`; handlers convert them to the uniform error JSON.
- Logging is structured (`time level logger message` + `key=value`), with
  human-readable stage markers (`UPLOAD START`, `FFPROBE`, `FRAME EXTRACTION`,
  `DEDUPLICATION`, `EMBEDDING`, `CHROMADB INDEXING`, `JOB COMPLETE`).

## 6. Debugging tips

- `python -m app.cli doctor` — one-shot environment check.
- `GET /api/health` and `GET /api/system/info` expose runtime state.
- `backend.log` under `data/logs/` keeps the full technical detail that the
  API intentionally redacts.
- Recreate a clean state by deleting `data/` (indexes + media + DB) and
  restarting the backend — configuration is unaffected.
