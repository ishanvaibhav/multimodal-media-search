"""Response envelope, request IDs, health endpoints (plan §45–§47)."""


def test_health_endpoints(client):
    for path in ("/health", "/health/live", "/health/ready"):
        r = client.get(path)
        assert r.status_code == 200, path
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["components"]["database"]["status"] == "ok"


def test_success_envelope_shape(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer dev:a@example.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["email"] == "a@example.com"
    assert body["data"]["role"] == "ADMIN"  # first user bootstrap
    assert "permissions" in body["data"]


def test_error_envelope_shape_and_request_id(client):
    r = client.get("/api/auth/me")  # no token
    assert r.status_code == 401
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "UNAUTHENTICATED"
    assert body["error"]["request_id"] is not None
    assert r.headers["X-Request-ID"] == body["error"]["request_id"]


def test_request_id_is_propagated_from_client(client):
    r = client.get("/health", headers={"X-Request-ID": "trace-123"})
    assert r.headers["X-Request-ID"] == "trace-123"


def test_validation_error_envelope(client, admin_headers):
    r = client.patch(
        "/api/auth/profile",
        headers=admin_headers,
        json={"display_name": ""},  # min_length violation
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_route_uses_envelope(client):
    r = client.get("/api/definitely-not-here")
    assert r.status_code == 404
    assert r.json()["success"] is False
