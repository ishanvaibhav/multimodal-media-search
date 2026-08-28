"""Fine-search accuracy: verify fine search narrows/keeps temporal precision
and that accurate-mode results are traceable to the fine stage."""
from __future__ import annotations

import pytest

from . import build_demo_video, index_demo_video


@pytest.mark.ml
def test_fine_search_temporal_precision(container, tmp_path):
    if container.embedding.name != "siglip" or not container.embedding.semantic:
        pytest.skip("requires SigLIP")
    demo = build_demo_video(tmp_path)
    index_demo_video(container, demo)

    svc = container.search_service
    svc.settings.fine_search_max_frames = 100
    svc.settings.fine_search_window_seconds = 4.0
    svc.settings.fine_frame_interval_seconds = 0.5
    svc.settings.fine_search_concurrency = 2

    query = "a cat"
    seg_start, seg_end = 8.0, 16.0  # cat segment occupies [8, 16)
    tol = 3.0

    coarse = svc.search(query, {"mode": "fast", "fine_search": False, "final_results": 1})
    fine = svc.search(query, {"mode": "accurate", "fine_search": True, "final_results": 1})

    assert coarse["results"], "coarse search returned nothing"
    assert fine["results"], "accurate search returned nothing"

    coarse_ts = coarse["results"][0]["timestamp"]
    fine_ts = fine["results"][0]["timestamp"]

    # Invariant 1: the fine result must stay inside the correct temporal
    # segment (within tolerance of its edges) — it must never jump to a
    # different segment.
    assert seg_start - tol <= fine_ts <= seg_end + tol, (
        f"fine result {fine_ts} escaped the cat segment [{seg_start},{seg_end}]"
    )

    # Invariant 2: the coarse result must also identify the correct segment
    # (a coarse miss would be a retrieval failure, not a fine-search failure).
    assert seg_start - tol <= coarse_ts <= seg_end + tol, (
        f"coarse result {coarse_ts} outside the cat segment"
    )

    # Invariant 3: accurate mode must actually refine via the fine stage
    assert any(r["retrieval_stage"] == "fine" for r in fine["results"]), (
        "accurate mode did not use fine search"
    )

    # Report (not assert) the measured precision — fine search benefit is
    # segment-resolution, and coarse may land exactly on a boundary.
    print(
        f"\n[fine-search precision] query={query!r} coarse={coarse_ts:.2f}s "
        f"fine={fine_ts:.2f}s segment=[{seg_start},{seg_end}]"
    )


@pytest.mark.ml
def test_fine_search_no_cross_video_contamination(container, tmp_path):
    if container.embedding.name != "siglip" or not container.embedding.semantic:
        pytest.skip("requires SigLIP")
    demo = build_demo_video(tmp_path)
    index_demo_video(container, demo)

    svc = container.search_service
    r = svc.search("a cat", {"mode": "accurate", "fine_search": True, "final_results": 3})
    for res in r["results"]:
        assert res["video_id"] == "evaldemo"
        assert res["trace"]["video_id"] == "evaldemo"
    # fine-cache frames are all scoped to the single video
    manifests = container.fine_cache_repo.all_intervals()
    assert all(m["video_id"] == "evaldemo" for m in manifests)
