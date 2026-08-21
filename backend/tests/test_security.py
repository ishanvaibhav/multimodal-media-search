"""Security tests: admin auth, input/path hardening, resource limits."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _client(settings):
    from app.main import create_app

    app = create_app(settings)
    return TestClient(app)


# ---------------------------------------------------------------------------
def test_admin_open_in_development_without_token(settings):
    with _client(settings) as client:
        res = client.request("DELETE", "/api/admin/data", json={"confirmation": "wrong"})
        # confirmation still enforced even when auth is open
        assert res.status_code == 400


def test_admin_requires_token_when_configured(settings):
    settings = settings.model_copy(update={"admin_token": "sekret"})
    with _client(settings) as client:
        # no token -> 401
        res = client.request("DELETE", "/api/admin/data", json={"confirmation": "DELETE ALL"})
        assert res.status_code == 401
        # bad token -> 401
        res = client.request(
            "DELETE", "/api/admin/data",
            json={"confirmation": "DELETE ALL"}, headers={"x-admin-token": "nope"},
        )
        assert res.status_code == 401
        # good token -> 200
        res = client.request(
            "DELETE", "/api/admin/data",
            json={"confirmation": "DELETE ALL"}, headers={"x-admin-token": "sekret"},
        )
        assert res.status_code == 200


def test_admin_fails_closed_in_production_without_token(settings):
    from app.api.deps import AdminNotConfiguredError, authorize_admin

    prod = settings.model_copy(update={"app_env": "production", "admin_token": ""})
    with pytest.raises(AdminNotConfiguredError):
        authorize_admin(prod, "whatever")


def test_admin_fails_closed_in_production_bad_token(settings):
    from app.api.deps import UnauthorizedError, authorize_admin

    prod = settings.model_copy(update={"app_env": "production", "admin_token": "sekret"})
    with pytest.raises(UnauthorizedError):
        authorize_admin(prod, "")
    with pytest.raises(UnauthorizedError):
        authorize_admin(prod, "wrong")
    # correct token passes
    authorize_admin(prod, "sekret")


def test_admin_open_in_development_without_token(settings):
    from app.api.deps import authorize_admin

    authorize_admin(settings, "")  # no exception
    authorize_admin(settings, "anything")  # ignored in dev


# ---------------------------------------------------------------------------
def test_oversized_upload_rejected(container):
    from app.exceptions import UploadError

    with pytest.raises(UploadError):
        container.upload_service.init("big.mp4", container.settings.max_upload_size_bytes + 1)


def test_unsupported_extension_rejected(container):
    from app.exceptions import ValidationError

    with pytest.raises(ValidationError):
        container.upload_service.init("evil.exe", 1024)


def test_invalid_ids_rejected(container):
    from app.exceptions import ValidationError
    from app.utils import validate_id

    for bad in ("../x", "a/b", "has space", "", "x" * 200):
        with pytest.raises(ValidationError):
            validate_id(bad)


def test_chunk_out_of_range_rejected(container):
    import asyncio

    from app.exceptions import UploadError

    up = container.upload_service.init("m.mp4", 5 * 1024 * 1024)

    async def body():
        yield b"x"

    async def go():
        await container.upload_service.receive_chunk(up.upload_id, 9999, body())

    with pytest.raises(UploadError):
        asyncio.run(go())


def test_frame_serving_rejects_escaped_path(container):
    from app.exceptions import StorageError

    with pytest.raises(StorageError):
        container.storage.resolve_in(container.settings.frames_dir, "../media/x.mp4")


# ---------------------------------------------------------------------------
def test_search_input_bounds(container):
    import asyncio

    from app.exceptions import ValidationError
    from app.schemas.search import SearchRequest
    from pydantic import ValidationError as PydValidationError

    with pytest.raises(PydValidationError):
        SearchRequest(query="q", top_k=10_000)
    with pytest.raises(PydValidationError):
        SearchRequest(query="q", min_similarity=5.0)


def test_bad_search_mode_rejected(container):
    from app.exceptions import ValidationError

    with pytest.raises(ValidationError):
        container.search_service.search("q", {"mode": "bogus"})
