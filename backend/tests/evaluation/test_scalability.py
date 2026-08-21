"""Scalability primitives: keyset pagination + synthetic scale smoke."""
from __future__ import annotations

from app.domain.models import Video
from app.utils import now_iso


def _seed(container, n: int):
    for i in range(n):
        vid = f"scale{i:05d}"
        container.video_repo.insert(Video(
            video_id=vid, filename=f"{vid}.mp4", original_filename=f"video_{i}.mp4",
            path=f"media/{vid}.mp4", size_bytes=1000, duration_seconds=60.0,
            status="ready",
            uploaded_at=f"2026-08-{(i % 28) + 1:02d}T10:00:00+00:00",
            created_at=now_iso(), updated_at=now_iso(),
        ))


def test_keyset_pagination_covers_all_without_duplicates(container):
    _seed(container, 57)
    seen = set()
    cursor = None
    pages = 0
    while True:
        page = container.video_repo.list_keyset(cursor, limit=20)
        if not page:
            break
        for v in page:
            assert v.video_id not in seen, f"duplicate {v.video_id}"
            seen.add(v.video_id)
        last = page[-1]
        cursor = (last.uploaded_at or "", last.video_id)
        pages += 1
        if pages > 10:
            break
    assert len(seen) == 57
    assert pages == 3  # 57 / 20 -> 3 pages


def test_keyset_pagination_ordering_desc(container):
    _seed(container, 5)
    page = container.video_repo.list_keyset(None, limit=100)
    keys = [(v.uploaded_at, v.video_id) for v in page]
    assert keys == sorted(keys, reverse=True)


def test_synthetic_scale_benchmark_smoke(tmp_path):
    from app.bench import bench_synthetic
    from app.config import Settings

    s = Settings(data_dir=str(tmp_path / "d"), chroma_path=str(tmp_path / "d" / "chroma"),
                 embedding_backend="deterministic", _env_file=None)
    report = bench_synthetic(s, video_counts=[20], frames_per_video=5)
    r = report["results"][0]
    assert r["videos"] == 20
    assert r["vectors"] == 100
    assert r["search_p50_ms"] >= 0
    assert r["page_rows"] <= 100
    assert report["label"].startswith("synthetic")
