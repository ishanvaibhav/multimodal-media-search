"""HTTP Range + embedding model-mismatch regression tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.models import Video
from app.utils import now_iso


def _fake_video_file(path: Path, size: int = 1000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(size))
    return path


def _client_with_video(settings, tmp_path, size=1000):
    from app.main import create_app

    app = create_app(settings)
    client = TestClient(app)
    client.__enter__()
    container = app.state.container
    dest = container.settings.media_dir / "vid.mp4"
    _fake_video_file(dest, size)
    container.video_repo.insert(Video(
        video_id="vid", filename="vid.mp4", original_filename="vid.mp4",
        path=container.storage.to_stored_path(dest), size_bytes=dest.stat().st_size,
        status="ready", uploaded_at=now_iso(), created_at=now_iso(), updated_at=now_iso(),
    ))
    return client, container


# ---------------------------------------------------------------------------
def test_range_full_request(settings, tmp_path):
    client, _ = _client_with_video(settings, tmp_path, size=1000)
    try:
        r = client.get("/api/media/vid/stream")
        assert r.status_code == 200
        assert len(r.content) == 1000
    finally:
        client.__exit__(None, None, None)


def test_range_valid_prefix(settings, tmp_path):
    client, _ = _client_with_video(settings, tmp_path, size=1000)
    try:
        r = client.get("/api/media/vid/stream", headers={"Range": "bytes=0-99"})
        assert r.status_code == 206
        assert r.headers["content-range"] == "bytes 0-99/1000"
        assert len(r.content) == 100
    finally:
        client.__exit__(None, None, None)


def test_range_open_ended(settings, tmp_path):
    client, _ = _client_with_video(settings, tmp_path, size=1000)
    try:
        r = client.get("/api/media/vid/stream", headers={"Range": "bytes=500-"})
        assert r.status_code == 206
        assert r.headers["content-range"] == "bytes 500-999/1000"
        assert len(r.content) == 500
    finally:
        client.__exit__(None, None, None)


def test_range_suffix(settings, tmp_path):
    client, _ = _client_with_video(settings, tmp_path, size=1000)
    try:
        r = client.get("/api/media/vid/stream", headers={"Range": "bytes=-100"})
        assert r.status_code == 206
        assert r.headers["content-range"] == "bytes 900-999/1000"
        assert len(r.content) == 100
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize(
    "range_header",
    ["bytes=100-50", "bytes=999999999999-", "bytes=abc", "bytes=-0", "bytes=", "bytes=0-1,5-6"],
)
def test_range_invalid_returns_416(settings, tmp_path, range_header):
    client, _ = _client_with_video(settings, tmp_path, size=1000)
    try:
        r = client.get("/api/media/vid/stream", headers={"Range": range_header})
        assert r.status_code == 416, f"{range_header} -> {r.status_code}"
        assert r.headers["content-range"] == "bytes */1000"
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Embedding model mismatch fail-closed
# ---------------------------------------------------------------------------
def _write_chroma(path: Path, dim: int, model="siglip", preproc="siglip-1", version="rev-1"):
    import chromadb

    c = chromadb.PersistentClient(path=str(path))
    col = c.get_or_create_collection("media_embeddings", metadata={"hnsw:space": "cosine"})
    col.add(
        ids=["f1"], embeddings=[[0.0] * dim],
        metadatas=[{
            "video_id": "v", "frame_id": "f1", "timestamp": 1.0,
            "embedding_model": model, "model_version": version,
            "preprocessing_version": preproc, "indexing_version": "1",
        }],
    )


def test_model_dimension_mismatch_fails(settings, tmp_path):
    from app.config import Settings
    from app.container import build_container
    from app.exceptions import VectorStoreError

    data = tmp_path / "data"
    chroma = data / "chroma"
    chroma.mkdir(parents=True, exist_ok=True)
    _write_chroma(chroma, 768, model="siglip")  # 768-dim existing index

    s = Settings(
        data_dir=str(data), chroma_path=str(chroma),
        embedding_backend="deterministic",  # 384-dim
        _env_file=None,
    )
    with pytest.raises(VectorStoreError):
        build_container(s)


def test_model_mismatch_same_dimension_fails_by_default(settings, tmp_path):
    from app.config import Settings
    from app.container import build_container
    from app.exceptions import VectorStoreError

    data = tmp_path / "data"
    chroma = data / "chroma"
    chroma.mkdir(parents=True, exist_ok=True)
    _write_chroma(chroma, 384, model="some-other-model")

    s = Settings(
        data_dir=str(data), chroma_path=str(chroma),
        embedding_backend="deterministic",  # 384-dim but different model name
        _env_file=None,
    )
    with pytest.raises(VectorStoreError):
        build_container(s)


def test_model_mismatch_dev_override(settings, tmp_path):
    from app.config import Settings
    from app.container import build_container

    data = tmp_path / "data"
    chroma = data / "chroma"
    chroma.mkdir(parents=True, exist_ok=True)
    _write_chroma(chroma, 384, model="other-model")

    s = Settings(
        data_dir=str(data), chroma_path=str(chroma),
        embedding_backend="deterministic",
        allow_model_mismatch=True,  # explicit dev override
        _env_file=None,
    )
    c = build_container(s)
    assert c.vectorstore.model_mismatch is True


def test_model_match_no_mismatch_flag(settings, tmp_path):
    from app.config import Settings
    from app.container import build_container

    data = tmp_path / "data"
    chroma = data / "chroma"
    chroma.mkdir(parents=True, exist_ok=True)
    # deterministic embedding stamps model_name='deterministic-baseline',
    # preprocessing_version='det-1', no model_version
    _write_chroma(chroma, 384, model="deterministic-baseline", preproc="det-1", version="")

    s = Settings(
        data_dir=str(data), chroma_path=str(chroma),
        embedding_backend="deterministic",
        _env_file=None,
    )
    c = build_container(s)
    assert c.vectorstore.model_mismatch is False
