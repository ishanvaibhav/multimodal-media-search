# Security model (plan §43)

## Authentication

Firebase ID token → FastAPI verifies signature against Google's public certs
(`AUTH_MODE=firebase`) → local user lookup → status check → role load.
Passwords are **never** stored or proxied by this backend (Firebase client
SDK owns reset/change flows).

`AUTH_MODE=dev` (unsigned `dev:<email>` tokens) exists only for local
development and CI; `APP_ENV=production` + `AUTH_MODE=dev` **aborts startup**
(see `app/core/config.py`, `tests/test_config_safety.py`).

## Authorization

Permission-based RBAC enforced per endpoint (`require_permission`); see
`docs/RBAC.md`. Token claims are never trusted for roles.

## Implemented now

* Envelope-only errors (no stack traces to clients; internals logged with request id).
* Request-size/query validation via Pydantic; pagination caps (`MAX_PAGE_SIZE`).
* Unique-binding protection: a provisioned record binds exactly one Firebase uid (`ACCOUNT_CONFLICT`).
* Self-demotion / last-admin guards on user management.
* Fail-fast production config gate (SQLite/dev-auth rejected).
* Audit log for every privileged mutation.

## Landing with later phases

| Control | Phase |
|---|---|
| Upload validation: extension + MIME + **magic bytes** + size + SHA-256 | 4 |
| Path-safety: no `../`, absolute paths or symlink escapes; storage keys generated server-side | 4 |
| Range streaming with 200/206/416 correctness | 3 |
| Rate limiting & request-size middleware | 12 |
| Signed, expiring share links | V2 |
| Dependency/secret scanning in CI | 12 |

## Secret hygiene

* `.env` and any `*service*account*.json` are git-ignored; only `.env.example` templates are committed.
* `APP_SECRET` auto-generates per environment when unset; set it explicitly in production.
* Firebase web client config (`firebase-applet-config.json`) is public by design (API key identifies the project; security comes from Firebase rules + backend verification). Rotate if it was ever intended to be private.
