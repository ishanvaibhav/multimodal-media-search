# RBAC — roles, permissions, enforcement (plan §2, §7)

## Roles

| Role | Purpose |
|---|---|
| `ADMIN` | Everything, including user/system management |
| `VIDEO_EDITOR` | Upload + search + monitor; **cannot** delete media or manage users |
| `MEDIA_SEARCHER` | Search/view/download/save contexts only |

## Permission catalog

Single source of truth: `backend/app/auth/permissions.py`.

| Permission | ADMIN | VIDEO_EDITOR | MEDIA_SEARCHER |
|---|:-:|:-:|:-:|
| `media.view` | ✅ | ✅ | ✅ |
| `media.upload` | ✅ | ✅ | ❌ |
| `media.download` | ✅ | ✅ | ✅ |
| `media.delete` | ✅ | ❌ | ❌ |
| `media.reindex` | ✅ | ❌ | ❌ |
| `search.execute` | ✅ | ✅ | ✅ |
| `search.feedback` | ✅ | ✅ | ✅ |
| `context.create/delete/export` | ✅ | ✅ | ✅ |
| `job.view` | ✅ | ✅ | ❌ |
| `job.cancel` | ✅ | ✅ | ❌ |
| `user.view/create/update/delete` | ✅ | ❌ | ❌ |
| `system.view/maintenance/repair/clear_data` | ✅ | ❌ | ❌ |
| `audit.view` | ✅ | ❌ | ❌ |

## Enforcement

```python
@router.get("/users", ...)
def list_users(_: User = require_permission(Permission.USER_VIEW), ...):
```

* Guards run in the **backend** on every request (plan §70 rule 1).
* The frontend receives the caller's permission set from `GET /api/auth/me`
  and hides forbidden UI as a convenience only.

## User lifecycle

```text
(provisioned by admin)          (first Firebase login)        (admin action)
email record PENDING  ──bind──▶  uid bound, ACTIVE  ◀────▶  DEACTIVATED
```

* `PENDING` users can authenticate; the first login activates them.
* `DEACTIVATED` users are rejected on **every** endpoint (`ACCOUNT_DEACTIVATED`).
* Self-service lockout guards: an admin cannot change their own role or
  deactivate themselves; the last active admin cannot be deactivated.

## Bootstrap (chicken-and-egg)

* `BOOTSTRAP_ADMIN_EMAIL` (env): that email claims the ADMIN seat on first login.
* Otherwise the very first authenticated user becomes ADMIN; in
  `APP_ENV=production`, unprovisioned identities are then **rejected**
  (`ACCOUNT_NOT_PROVISIONED`) — fail closed.
* In non-production, unknown users auto-provision as `MEDIA_SEARCHER` for DX.

## Auth modes

| `AUTH_MODE` | Use |
|---|---|
| `firebase` | Verify Google-issued ID tokens (needs only `FIREBASE_PROJECT_ID`; public certs fetched at runtime). Required when `APP_ENV=production`. |
| `dev` | Unsigned `Authorization: Bearer dev:<email>` tokens for local dev/CI. Forbidden in production (startup aborts). |
