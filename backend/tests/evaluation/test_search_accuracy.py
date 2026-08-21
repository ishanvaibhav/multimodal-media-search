"""Search-accuracy regression tests against the golden dataset.

Requires the real SigLIP model (`-m ml`): semantic retrieval accuracy cannot
be evaluated with the deterministic fallback (which is intentionally not
multimodal). These tests build a deterministic demo video, index it, run the
golden queries and assert Recall@K / MRR / temporal accuracy stay above
thresholds — a regression gate for embedding/sampling/grouping changes.
"""
from __future__ import annotations

import json

import pytest

from . import REPO_ROOT, build_demo_video, index_demo_video


@pytest.mark.ml
def test_golden_dataset_accuracy(container, tmp_path):
    from app.exceptions import ModelUnavailableError

    if container.embedding.name != "siglip" or not container.embedding.semantic:
        pytest.skip("requires the real SigLIP model (EMBEDDING_BACKEND=siglip)")

    demo = build_demo_video(tmp_path)
    index_demo_video(container, demo)

    dataset = json.loads((REPO_ROOT / "backend" / "evaluation" / "golden_dataset.json").read_text())
    tolerance = float(dataset.get("tolerance_seconds", 3.0))
    k = 5

    svc = container.search_service
    hits_1 = hits_5 = 0
    rr_sum = 0.0
    temporal_ok = 0
    n = 0
    details = []

    for item in dataset["queries"]:
        query = item["query"]
        expected = {round(float(t), 2) for t in item["expected"]}
        res = svc.search(query, {"mode": "fast", "fine_search": False, "final_results": k})
        retrieved = [
            (r["video_id"], round(float(r["timestamp"]), 2)) for r in res["results"][:k]
        ]
        ts_hits = [t for (v, t) in retrieved if v == "evaldemo" and t in expected]
        if any(t in expected for (_, t) in retrieved[:1]):
            hits_1 += 1
        if any(t in expected for (_, t) in retrieved[:5]):
            hits_5 += 1
        for rank, (_, t) in enumerate(retrieved, start=1):
            if t in expected:
                rr_sum += 1.0 / rank
                break
        best_err = min(
            (abs(t - e) for (_, t) in retrieved for e in expected), default=None
        )
        if best_err is not None and best_err <= tolerance:
            temporal_ok += 1
        n += 1
        details.append({
            "query": query,
            "retrieved": retrieved,
            "expected": sorted(expected),
            "temporal_error_s": best_err,
        })

    recall_1 = hits_1 / n
    recall_5 = hits_5 / n
    mrr = rr_sum / n
    temporal = temporal_ok / n

    # regression thresholds (documented; conservative to avoid flakiness)
    assert recall_1 >= 0.5, f"recall@1 {recall_1:.2f} < 0.5\n{details}"
    assert recall_5 >= 0.75, f"recall@5 {recall_5:.2f} < 0.75\n{details}"
    assert mrr >= 0.6, f"MRR {mrr:.2f} < 0.6\n{details}"
    assert temporal >= 0.7, f"temporal accuracy {temporal:.2f} < 0.7\n{details}"


@pytest.mark.ml
def test_result_traceability_fields(container, tmp_path):
    if container.embedding.name != "siglip" or not container.embedding.semantic:
        pytest.skip("requires SigLIP")
    demo = build_demo_video(tmp_path)
    index_demo_video(container, demo)
    res = container.search_service.search(
        "a dog", {"mode": "fast", "fine_search": False, "final_results": 2}
    )
    assert res["results"]
    r = res["results"][0]
    # every result is traceable to video/frame/stage/score/model
    assert r["video_id"] == "evaldemo"
    assert r["frame_id"]
    assert r["retrieval_stage"] in ("coarse", "fine")
    trace = r["trace"]
    assert trace["video_id"] == "evaldemo"
    assert trace["frame_id"] == r["frame_id"]
    assert trace["embedding_model"]
    assert "indexing_version" in trace
