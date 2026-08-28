"""Storage-consistency checker and recovery tests."""
from __future__ import annotations

import shutil
from pathlib import Path

from app.domain.models import Job, Video
from app.utils import now_iso


def _insert_video(container, video_id="vc", path=None):
    dest = path or (container.settings.media_dir / f"{video_id}.mp4")
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    container.video_repo.insert(Video(
        video_id=video_id, filename=f"{video_id}.mp4", original_filename=f"{video_id}.mp4",
        path=container.storage.to_stored_path(dest), size_bytes=dest.stat().st_size,
        status="ready", uploaded_at=now_iso(), created_at=now_iso(), updated_at=now_iso(),
    ))
    return dest


def test_consistency_detects_orphan_job(container):
    _insert_video(container, "v1")
    container.job_repo.insert(Job(job_id="j_orphan", video_id="does_not_exist"))
    report = container.consistency_service.check()
    assert any(o["job_id"] == "j_orphan" for o in report["orphan_jobs"])


def test_consistency_detects_missing_file(container):
    dest = _insert_video(container, "v2")
    dest.unlink()
    report = container.consistency_service.check()
    assert any(m["kind"] == "video" for m in report["missing_files"])


def test_consistency_detects_orphan_and_missing_vectors(container):
    from app.domain.models import Frame

    dest = _insert_video(container, "v3")
    # a frame in DB with no vector
    container.frame_repo.insert_many([Frame(
        frame_id="v3_000001", video_id="v3", timestamp_seconds=1.0,
        frame_path="frames/v3/x.jpg",
    )])
    # a vector in Chroma with no DB frame
    import numpy as np

    container.vectorstore.upsert(
        ["ghost_vector"], np.zeros((1, container.embedding.dim), dtype=np.float32),
        [{"video_id": "v3", "timestamp": 2.0, "frame_id": "ghost_vector"}],
    )
    report = container.consistency_service.check()
    assert "v3_000001" in report["missing_vectors"]
    assert "ghost_vector" in report["orphan_vectors"]


def test_consistency_repair_removes_orphans(container):
    import numpy as np

    dest = _insert_video(container, "v4")
    container.vectorstore.upsert(
        ["ghost2"], np.zeros((1, container.embedding.dim), dtype=np.float32),
        [{"video_id": "v4", "timestamp": 1.0, "frame_id": "ghost2"}],
    )
    report = container.consistency_service.check(repair=True)
    assert report["repaired"]["orphan_vectors"] == 1
    after = container.consistency_service.check()
    assert "ghost2" not in after["orphan_vectors"]


def test_recovery_marks_interrupted_job_failed(container):
    from app.domain.models import Frame

    dest = _insert_video(container, "v5")
    container.job_repo.insert(Job(job_id="j_interrupted", video_id="v5", status="running"))
    # simulate a partial index
    container.frame_repo.insert_many([Frame(
        frame_id="v5_000001", video_id="v5", timestamp_seconds=1.0,
        frame_path="frames/v5/x.jpg",
    )])
    import numpy as np

    container.vectorstore.upsert(
        ["v5_000001"], np.zeros((1, container.embedding.dim), dtype=np.float32),
        [{"video_id": "v5", "timestamp": 1.0, "frame_id": "v5_000001"}],
    )

    result = container.recovery_service.recover_interrupted_jobs()
    assert result["interrupted"] >= 1
    assert container.job_repo.get("j_interrupted").status == "failed"
    assert container.video_repo.get("v5").status == "failed"
    # partial index rolled back
    assert container.frame_repo.count_for_video("v5") == 0
    assert container.vectorstore.all_ids_for_video("v5") == set()


def test_relative_path_migration(container):
    from app.domain.models import Frame

    data_root = container.settings.data_dir_path.resolve()
    absolute = str(data_root / "media" / "old.mp4")
    dest = data_root / "media" / "old.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"x" * 8)
    container.video_repo.insert(Video(
        video_id="vm", filename="old.mp4", original_filename="old.mp4",
        path=absolute, size_bytes=8, status="ready",
        uploaded_at=now_iso(), created_at=now_iso(), updated_at=now_iso(),
    ))
    counts = container.recovery_service.normalize_paths()
    assert counts["videos"] >= 1
    assert container.video_repo.get("vm").path == "media/old.mp4"


# ---------------------------------------------------------------------------
# Fine-cache consistency + reconciliation
# ---------------------------------------------------------------------------
def test_consistency_detects_orphan_manifest(container):
    _insert_video(container, "v_fc1")
    container.fine_cache_repo.add_interval(
        "does_not_exist", 250, 0.0, 4.0, 16, 17, "fine-v1"
    )
    report = container.consistency_service.check()
    assert report["orphan_manifest_count"] == 1


def test_consistency_detects_invalid_manifest_missing_frames(container):
    _insert_video(container, "v_fc2")
    # manifest claims a complete interval but no frames exist
    container.fine_cache_repo.add_interval(
        "v_fc2", 250, 0.0, 4.0, 16, 17, "fine-v1"
    )
    report = container.consistency_service.check()
    assert report["invalid_manifest_count"] >= 1


def test_consistency_repair_removes_invalid_manifests(container):
    _insert_video(container, "v_fc3")
    container.fine_cache_repo.add_interval(
        "v_fc3", 250, 0.0, 4.0, 16, 17, "fine-v1"  # frames missing -> invalid
    )
    container.fine_cache_repo.add_interval(
        "gone", 250, 0.0, 4.0, 16, 17, "fine-v1"   # orphan
    )
    report = container.consistency_service.check(repair=True)
    assert report["repaired"]["invalid_manifests"] >= 1
    assert report["repaired"]["orphan_manifests"] >= 1
    after = container.consistency_service.check()
    assert after["invalid_manifest_count"] == 0
    assert after["orphan_manifest_count"] == 0


def test_consistency_model_registry_check(container):
    report = container.consistency_service.check()
    mc = report["model_consistency"]
    assert "registry_model" in mc and "chroma_models" in mc
    assert mc["consistent"] is True
