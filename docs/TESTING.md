# Testing strategy (plan §54)

Run: `cd backend && pytest` (uses per-test isolated SQLite; nothing leaves the tmp dir).

## Current coverage

| Suite | Covers |
|---|---|
| `test_permissions.py` | Full role×permission matrix; admin-only powers denied to other roles |
| `test_rbac_api.py` | Provisioning/JIT binding, 401/403 enforcement, duplicate-create conflict, role change + audit, self/last-admin guards, deactivation rejection, filters & pagination, profile update |
| `test_envelope_and_health.py` | `Ok`/`Error` envelope shapes, `X-Request-ID` echo, validation-error shape, unknown-route envelope, health/live/ready |
| `test_config_safety.py` | Production fail-fast gate; job state machine absorbs terminal states |

## Levels (target per plan)

1. **Unit** — permissions, schemas, grouping/dedup, path validation.
2. **Integration** — API + DB via FastAPI TestClient (this suite).
3. **ML** — embedding dims, retrieval quality on a golden dataset (§52), fine-search accuracy.
4. **Security** — unauthorized access (done), role escalation (done), path traversal, bad MIME, oversized upload, expired token.
5. **Concurrency** — duplicate upload completion, delete-while-indexing, parallel fine searches.
6. **E2E** — login → upload → index → search → play → save → export.

## Conventions

* Tests never contact Firebase, HF Hub, or the network — `AUTH_MODE=dev` provides identities; ML tests use the `deterministic` embedding backend.
* Every bug fix ships with a failing→passing test (plan §70 rule 10).
* Performance budgets (search p50/p95) get asserted in the load suite — Phase 13.
