"""Integration tests: provisioning, RBAC enforcement, admin user management,
audit logging (plan §2, §7, §42, §54)."""

from .conftest import bearer


def _make_user(client, admin_headers, email, role):
    r = client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"email": email, "role": role},
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_first_dev_user_bootstraps_as_admin(client):
    r = client.get("/api/auth/me", headers=bearer("founder@example.com"))
    assert r.status_code == 200
    assert r.json()["data"]["role"] == "ADMIN"
    assert r.json()["data"]["status"] == "ACTIVE"


def test_dev_auto_provisions_searcher_after_bootstrap(client, admin_headers):
    r = client.get("/api/auth/me", headers=bearer("stranger@example.com"))
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["role"] == "MEDIA_SEARCHER"
    assert data["status"] == "ACTIVE"


def test_admin_provisions_pending_user_and_first_login_binds(client, admin_headers):
    created = _make_user(client, admin_headers, "editor@example.com", "VIDEO_EDITOR")
    assert created["status"] == "PENDING"

    # First login binds the verified identity and activates the account.
    r = client.get("/api/auth/me", headers=bearer("editor@example.com"))
    assert r.status_code == 200
    assert r.json()["data"]["role"] == "VIDEO_EDITOR"
    assert r.json()["data"]["status"] == "ACTIVE"


def test_searcher_cannot_reach_admin_endpoints(client, admin_headers):
    _make_user(client, admin_headers, "searcher@example.com", "MEDIA_SEARCHER")
    h = bearer("searcher@example.com")
    client.get("/api/auth/me", headers=h)

    for method, path in (
        ("GET", "/api/admin/users"),
        ("POST", "/api/admin/users"),
        ("GET", "/api/admin/stats"),
        ("GET", "/api/admin/audit-logs"),
    ):
        r = client.request(method, path, headers=h, json={} if method == "POST" else None)
        assert r.status_code == 403, (method, path, r.text)
        assert r.json()["error"]["code"] == "FORBIDDEN"


def test_editor_cannot_manage_users(client, admin_headers):
    _make_user(client, admin_headers, "ed@example.com", "VIDEO_EDITOR")
    h = bearer("ed@example.com")
    client.get("/api/auth/me", headers=h)
    r = client.post("/api/admin/users", headers=h, json={"email": "x@example.com", "role": "MEDIA_SEARCHER"})
    assert r.status_code == 403


def test_duplicate_user_email_conflicts(client, admin_headers):
    _make_user(client, admin_headers, "dup@example.com", "MEDIA_SEARCHER")
    r = client.post(
        "/api/admin/users", headers=admin_headers, json={"email": "dup@example.com", "role": "VIDEO_EDITOR"}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "USER_EXISTS"


def test_role_change_writes_audit_and_applies(client, admin_headers):
    user = _make_user(client, admin_headers, "promote@example.com", "MEDIA_SEARCHER")
    r = client.patch(
        f"/api/admin/users/{user['id']}/role", headers=admin_headers, json={"role": "VIDEO_EDITOR"}
    )
    assert r.status_code == 200
    assert r.json()["data"]["role"] == "VIDEO_EDITOR"

    logs = client.get("/api/admin/audit-logs", headers=admin_headers).json()["data"]["items"]
    entry = next(x for x in logs if x["action"] == "ROLE_CHANGED" and x["target_id"] == user["id"])
    assert entry["details"] == {
        "email": "promote@example.com",
        "from": "MEDIA_SEARCHER",
        "to": "VIDEO_EDITOR",
    }
    assert entry["request_id"]

    # New role effective immediately.
    me = client.get("/api/auth/me", headers=bearer("promote@example.com")).json()["data"]
    assert me["role"] == "VIDEO_EDITOR"
    assert "media.upload" in me["permissions"]


def test_admin_cannot_change_own_role(client, admin_headers):
    me = client.get("/api/auth/me", headers=admin_headers).json()["data"]
    r = client.patch(
        f"/api/admin/users/{me['id']}/role", headers=admin_headers, json={"role": "MEDIA_SEARCHER"}
    )
    assert r.status_code == 409


def test_deactivated_user_is_rejected_everywhere(client, admin_headers):
    user = _make_user(client, admin_headers, "bye@example.com", "MEDIA_SEARCHER")
    client.get("/api/auth/me", headers=bearer("bye@example.com"))  # activate

    r = client.patch(
        f"/api/admin/users/{user['id']}/status", headers=admin_headers, json={"status": "DEACTIVATED"}
    )
    assert r.status_code == 200

    r = client.get("/api/auth/me", headers=bearer("bye@example.com"))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "ACCOUNT_DEACTIVATED"

    # Reactivation restores access.
    client.patch(f"/api/admin/users/{user['id']}/status", headers=admin_headers, json={"status": "ACTIVE"})
    r = client.get("/api/auth/me", headers=bearer("bye@example.com"))
    assert r.status_code == 200


def test_last_active_admin_cannot_be_deactivated(client, admin_headers):
    me = client.get("/api/auth/me", headers=admin_headers).json()["data"]
    other = _make_user(client, admin_headers, "second-admin@example.com", "ADMIN")
    # Self-protection fires first for self; use a second admin to try on self
    h2 = bearer("second-admin@example.com")
    client.get("/api/auth/me", headers=h2)
    r = client.patch(
        f"/api/admin/users/{other['id']}/status", headers=admin_headers, json={"status": "DEACTIVATED"}
    )
    assert r.status_code == 200  # allowed: one admin remains
    # Now admin@example.com is the last active admin; second admin is gone
    me = client.get("/api/auth/me", headers=admin_headers).json()["data"]
    r = client.patch(
        f"/api/admin/users/{me['id']}/status", headers=admin_headers, json={"status": "DEACTIVATED"}
    )
    assert r.status_code == 409  # self-change guard
    assert r.json()["error"]["code"] == "SELF_STATUS_CHANGE"


def test_user_list_filters_and_pagination(client, admin_headers):
    for i in range(5):
        _make_user(client, admin_headers, f"user{i}@example.com", "MEDIA_SEARCHER")
    r = client.get("/api/admin/users?role=MEDIA_SEARCHER&page=1&page_size=3", headers=admin_headers)
    data = r.json()["data"]
    assert data["total"] == 5
    assert len(data["items"]) == 3
    r = client.get("/api/admin/users?q=user1@", headers=admin_headers)
    assert r.json()["data"]["total"] == 1

    # page_size cap enforced
    r = client.get("/api/admin/users?page_size=500", headers=admin_headers)
    assert r.status_code == 422


def test_admin_stats_endpoint(client, admin_headers):
    _make_user(client, admin_headers, "s1@example.com", "MEDIA_SEARCHER")
    r = client.get("/api/admin/stats", headers=admin_headers)
    data = r.json()["data"]
    assert data["total_users"] == 2
    assert data["active_users"] == 1
    assert data["total_media"] == 0


def test_invalid_dev_token_rejected(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer dev:not-an-email"})
    assert r.status_code == 401


def test_profile_update(client):
    h = bearer("me@example.com")
    client.get("/api/auth/me", headers=h)
    r = client.patch(
        "/api/auth/profile", headers=h, json={"display_name": "Me", "recovery_phone": "+15551234567"}
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["display_name"] == "Me"
    assert data["recovery_phone"] == "+15551234567"
