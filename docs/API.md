# API contract — v1 (plan §44, §45)

Base URL: `http://localhost:8000` · Auth: `Authorization: Bearer <Firebase ID token>`

## Envelope

Every response is wrapped:

```jsonc
// success
{ "success": true, "data": { /* payload */ } }

// error
{ "success": false, "error": { "code": "MEDIA_NOT_FOUND", "message": "...", "request_id": "..." } }
```

Every response carries an `X-Request-ID` header (propagated if the client
sends one). Lists use `{"items": [...], "total", "page", "page_size"}`;
`page_size` is capped at `MAX_PAGE_SIZE` (100).

Interactive OpenAPI (dev only): `/docs`.

## Implemented

| Method/Path | Permission | Notes |
|---|---|---|
| `GET /health` `/health/live` `/health/ready` | – | liveness/readiness (§47) |
| `GET /api/system/version` | – | version surface (§58) |
| `GET /api/auth/me` | authenticated | profile + role + permission set |
| `PATCH /api/auth/profile` | authenticated | display name, recovery phone |
| `GET /api/admin/stats` | `system.view` | dashboard counters (§40) |
| `GET /api/admin/users` | `user.view` | filters: `role`, `status`, `q`; paginated |
| `POST /api/admin/users` | `user.create` | pre-provision by email → `PENDING` |
| `PATCH /api/admin/users/{id}/role` | `user.update` | audited `ROLE_CHANGED`; no self-change |
| `PATCH /api/admin/users/{id}/status` | `user.update` | audited; last-admin/self guards |
| `GET /api/admin/audit-logs` | `audit.view` | filter by `action`; paginated |

## Roadmap (land in their phases)

| Group | Endpoints | Phase |
|---|---|---|
| Media | `GET/GET one/DELETE /api/media*`, `POST /api/media/{id}/reindex`, `GET .../thumbnail`, `.../frames/{fid}`, `.../stream` (Range/206/416) | 3, 7 |
| Uploads | `GET /api/uploads/config`, `POST /api/uploads/init`, `POST .../chunk`, `GET .../status`, `POST .../complete`, `DELETE ...` | 4 |
| Search | `POST /api/search`, `GET/DELETE /api/search/history`, `POST/GET /api/search/feedback` | 6–9 |
| Contexts | `POST/GET /api/contexts`, `DELETE .../{id}`, `GET /api/contexts/export` | 9 |
| Jobs | `GET /api/jobs`, `.../{id}`, `POST .../cancel` | 5 |
| Admin ops | `POST /api/admin/maintenance/start|stop`, `GET /api/admin/maintenance`, `POST /api/admin/data/clear`, consistency scan/repair | 10–11 |

## Error codes (stable vocabulary)

`UNAUTHENTICATED` · `FORBIDDEN` · `ACCOUNT_DEACTIVATED` ·
`ACCOUNT_NOT_PROVISIONED` · `ACCOUNT_CONFLICT` · `NOT_FOUND` /
`*_NOT_FOUND` · `USER_EXISTS` · `SELF_ROLE_CHANGE` · `SELF_STATUS_CHANGE` ·
`LAST_ADMIN` · `INVALID_STATUS` · `VALIDATION_ERROR` · `CONFLICT` ·
`RATE_LIMITED` · `INTERNAL_ERROR`
