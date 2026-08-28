"""Context service, saved contexts, and query-expansion regression tests."""
from __future__ import annotations

import json

import pytest

from app.application.context_service import format_context_text
from app.application.search_service import SearchService


# ---------------------------------------------------------------------------
# context formatting (plain text, no markup)
# ---------------------------------------------------------------------------
def test_format_context_text_is_plain_text():
    result = {
        "video_name": "sample_video.mp4",
        "timestamp": 332.4,
        "context_start": 324.0,
        "context_end": 341.0,
        "similarity": 0.87,
        "context_reason": "Frame at 00:05:32 matches ... (cosine similarity 0.870).",
        "frames": [
            {"timestamp": 325.0, "timestamp_hms": "00:05:25"},
            {"timestamp": 329.0, "timestamp_hms": "00:05:29"},
            {"timestamp": 332.0, "timestamp_hms": "00:05:32"},
        ],
    }
    text = format_context_text(result, "person near red car")
    assert "sample_video.mp4" in text
    assert "00:05:32" in text
    assert "00:05:24 - 00:05:41" in text
    assert "0.87" in text
    assert "<" not in text  # no markup
    assert "person near red car" in text
    # explicit section separation + representative frames
    assert "RETRIEVED EVIDENCE" in text
    assert "Representative frames:" in text
    assert "00:05:25" in text and "00:05:29" in text
    # no AI SUMMARY section when no summary supplied
    assert "AI SUMMARY" not in text


def test_format_context_text_with_summary_section():
    result = {
        "video_name": "demo.mp4",
        "timestamp": 16.0,
        "context_start": 8.0,
        "context_end": 24.0,
        "similarity": 0.8,
        "context_reason": "grounded reason",
    }
    text = format_context_text(result, "a red car", summary="A car is shown.")
    assert "RETRIEVED EVIDENCE" in text
    assert "AI SUMMARY" in text
    assert "A car is shown." in text
    # evidence comes before the summary
    assert text.index("RETRIEVED EVIDENCE") < text.index("AI SUMMARY")


# ---------------------------------------------------------------------------
# query normalization / expansion
# ---------------------------------------------------------------------------
def test_normalize_query(container):
    assert container.search_service._normalize_query('  "a dog"  ') == "a dog"


def test_expansion_single_component_unchanged(container):
    svc = container.search_service
    svc.settings.query_expansion = True
    # no connectors -> exactly the original query
    assert svc._expand_query("a dog") == ["a dog"]
    assert svc._expand_query("a red car") == ["a red car"]


def test_expansion_splits_connector_query(container):
    svc = container.search_service
    svc.settings.query_expansion = True
    components = svc._expand_query("person wearing a black shirt near a car")
    # full query always first; sub-components follow
    assert components[0] == "person wearing a black shirt near a car"
    joined = " | ".join(components).lower()
    assert "wearing a black shirt" in joined or "a black shirt" in joined
    assert "a car" in joined


def test_expansion_disabled_returns_single(container):
    svc = container.search_service
    svc.settings.query_expansion = False
    assert svc._expand_query("person near a car") == ["person near a car"]


def test_expansion_component_word_minimum(container):
    svc = container.search_service
    svc.settings.query_expansion = True
    svc.settings.query_expansion_min_words = 3
    components = svc._expand_query("a dog near a car")
    # "a car" is 2 words -> dropped; only the full query remains
    assert components == ["a dog near a car"]


# ---------------------------------------------------------------------------
# query embedding cache
# ---------------------------------------------------------------------------
def test_query_embedding_cache(container):
    svc = container.search_service
    q = "a unique test query"
    e1 = svc._embed_query(q)
    e2 = svc._embed_query(q)
    import numpy as np

    assert np.array_equal(np.asarray(e1), np.asarray(e2))
    assert svc._qemb_cache.get(q) is not None


# ---------------------------------------------------------------------------
# saved contexts (CRUD + export)
# ---------------------------------------------------------------------------
def _save(container, query="a dog", **over):
    payload = {
        "query": query,
        "video_id": "v123",
        "filename": "demo.mp4",
        "media_type": "video",
        "timestamp_seconds": 332.4,
        "context_start": 324.0,
        "context_end": 341.0,
        "score": 0.87,
        "frame_id": "v123_000001",
        "context_text": "ctx",
    }
    payload.update(over)
    return container.context_service.save(payload)


def test_saved_context_crud(container):
    saved = _save(container)
    assert saved.id is not None

    items = container.context_service.list()
    assert any(s.id == saved.id for s in items)

    assert container.context_service.delete(saved.id)["deleted"] == saved.id
    assert container.context_service.list() == []


def test_saved_context_export_formats(container):
    _save(container, query="a dog")
    _save(container, query="a red car", timestamp_seconds=16.0)

    txt = container.context_service.export("txt")
    assert txt["extension"] == "txt"
    assert "a dog" in txt["content"] and "a red car" in txt["content"]

    js = container.context_service.export("json")
    data = json.loads(js["content"])
    assert len(data) == 2
    assert data[0]["query"] in ("a red car", "a dog")
    assert "timestamp" in data[0] and "score" in data[0]

    csv = container.context_service.export("csv")
    assert "query,filename" in csv["content"]


def test_saved_context_delete_missing_raises(container):
    from app.exceptions import NotFoundError

    with __import__("pytest").raises(NotFoundError):
        container.context_service.delete(999999)


def test_saved_context_requires_query(container):
    from app.exceptions import ValidationError

    import pytest

    with pytest.raises(ValidationError):
        container.context_service.save({"query": "  ", "video_id": "v1"})


# ---------------------------------------------------------------------------
# search results carry temporal context
# ---------------------------------------------------------------------------
def test_search_result_has_context_fields(container, sample_video):
    import shutil

    from app.domain.models import Job, Video
    from app.utils import now_iso

    dest = container.settings.media_dir / "cx.mp4"
    shutil.copyfile(sample_video, dest)
    container.video_repo.insert(Video(
        video_id="cx", filename="cx.mp4", original_filename="cx.mp4",
        path=container.storage.to_stored_path(dest), size_bytes=dest.stat().st_size,
        duration_seconds=12.0, status="queued", uploaded_at=now_iso(),
        created_at=now_iso(), updated_at=now_iso(),
    ))
    job = Job(job_id="jcx", video_id="cx")
    container.job_repo.insert(job)
    container.indexing_service.run_job(job, lambda: False)
    assert container.video_repo.get("cx").status == "ready"

    r = container.search_service.search("a colorful pattern", {"mode": "fast", "final_results": 1})
    assert r["results"]
    res = r["results"][0]
    assert "context_start" in res and "context_end" in res
    assert "context_reason" in res and "context_text" in res
    assert res["context_reason"]  # grounded reason present
    # segment is within the video
    assert 0 <= res["context_start"] <= res["context_end"]


# ---------------------------------------------------------------------------
# Revision 6: representative context frame selection, rerank, fusion boost
# ---------------------------------------------------------------------------
def test_context_frame_selection_keeps_matched_and_orders(container, sample_video):
    import shutil
    from pathlib import Path

    from app.domain.models import Job, Video
    from app.utils import now_iso

    dest = container.settings.media_dir / "sel.mp4"
    shutil.copyfile(sample_video, dest)
    container.video_repo.insert(Video(
        video_id="sel", filename="sel.mp4", original_filename="sel.mp4",
        path=container.storage.to_stored_path(dest), size_bytes=dest.stat().st_size,
        duration_seconds=12.0, status="queued", uploaded_at=now_iso(),
        created_at=now_iso(), updated_at=now_iso(),
    ))
    job = Job(job_id="jsel", video_id="sel")
    container.job_repo.insert(job)
    container.indexing_service.run_job(job, lambda: False)
    assert container.video_repo.get("sel").status == "ready"

    frames = container.frame_repo.list_for_video("sel")
    assert len(frames) >= 2
    # synthetic representative selection: pick a mid timestamp
    from app.domain.models import Candidate

    rep = Candidate(
        frame_id=frames[len(frames) // 2].frame_id, video_id="sel",
        timestamp_seconds=frames[len(frames) // 2].timestamp_seconds,
        score=0.8, raw_score=0.08, frame_path="", video_path="",
    )
    selected = container.search_service._select_context_frames(frames, rep)
    # bounded count, chronological order, matched frame included
    assert 1 <= len(selected) <= container.settings.context_max_frames
    ts = [f.timestamp_seconds for f in selected]
    assert ts == sorted(ts)
    assert rep.frame_id in [f.frame_id for f in selected]


def test_deterministic_rerank_sets_final_score(container):
    from app.domain.models import Candidate

    svc = container.search_service
    svc.settings.rerank_weight_semantic = 1.0
    svc.settings.rerank_weight_neighbor = 0.1
    svc.settings.rerank_weight_diversity = 0.05
    e1 = [Candidate(frame_id="a1", video_id="A", timestamp_seconds=1, score=0.9, frame_path="", video_path="")]
    e2 = [Candidate(frame_id="a2", video_id="A", timestamp_seconds=30, score=0.5, frame_path="", video_path="")]
    e3 = [Candidate(frame_id="b1", video_id="B", timestamp_seconds=5, score=0.6, frame_path="", video_path="")]
    svc._rerank_events([e1, e2, e3])
    assert e1[0].final_score > e2[0].final_score  # semantic dominates
    # diversity bonus: video B's best event gets the bonus
    assert e3[0].final_score == pytest.approx(0.6 + 0.1 * 0.6 + 0.05, abs=1e-6)


def test_fusion_full_query_boost(container):
    """Candidates matched by the FULL query get a configurable boost so
    component queries improve recall without overriding precision."""
    import numpy as np

    from app.domain.models import Candidate

    svc = container.search_service
    svc.settings.fusion_method = "max"
    svc.settings.fusion_full_query_boost = 0.3

    # candidate scores BEFORE boost
    c_full = Candidate(frame_id="F1", video_id="v", timestamp_seconds=1,
                       score=0.4, frame_path="", video_path="")
    c_comp = Candidate(frame_id="F2", video_id="v", timestamp_seconds=2,
                       score=0.6, frame_path="", video_path="")

    # fake the vector store: full query returns F1, component query returns F2
    def fake_query(emb, top_k, where=None):
        return [c_full] if emb is q_full else [c_comp]

    q_full = object(); q_comp = object()
    orig = svc.vectorstore.query
    svc.vectorstore.query = fake_query
    try:
        merged = svc._fused_candidates([q_full, q_comp], top_k=10, where=None)
    finally:
        svc.vectorstore.query = orig

    by_id = {c.frame_id: c for c in merged}
    # F1 (matched by the full query) received the +0.3 boost: 0.4 -> 0.7
    assert by_id["F1"].score == pytest.approx(0.7, abs=1e-6)
    # F2 (component-only) is unchanged
    assert by_id["F2"].score == pytest.approx(0.6, abs=1e-6)
    # full-query candidate now ranks first (dominant)
    assert merged[0].frame_id == "F1"


def test_saved_context_roundtrip_frames_and_reason(container):
    saved = container.context_service.save({
        "query": "a dog",
        "video_id": "v1",
        "filename": "demo.mp4",
        "media_type": "video",
        "timestamp_seconds": 5.0,
        "context_start": 1.0,
        "context_end": 9.0,
        "score": 0.8,
        "frame_id": "v1_000001",
        "context_text": "ctx block",
        "context_frames": [
            {"frame_id": "v1_000001", "timestamp": 5.0, "timestamp_hms": "00:00:05"},
            {"frame_id": "v1_000002", "timestamp": 7.0, "timestamp_hms": "00:00:07"},
        ],
        "reason": "grounded reason",
    })
    items = container.context_service.list()
    got = next(s for s in items if s.id == saved.id)
    assert len(got.context_frames) == 2
    assert got.reason == "grounded reason"

    js = json.loads(container.context_service.export("json")["content"])
    entry = next(e for e in js if e["query"] == "a dog")
    assert entry["context_frames"] == got.context_frames
    assert entry["reason"] == "grounded reason"
    assert entry["context"] is not None


# ---------------------------------------------------------------------------
# Revision 7: full-query dominance in rerank, save-completeness regression
# ---------------------------------------------------------------------------
def test_rerank_full_query_bonus(container):
    from app.domain.models import Candidate

    svc = container.search_service
    svc.settings.rerank_weight_semantic = 1.0
    svc.settings.rerank_weight_full_query = 0.2
    svc.settings.rerank_weight_neighbor = 0.0
    svc.settings.rerank_weight_diversity = 0.0

    # same semantic score; the full-query match must rank first
    full = Candidate(frame_id="fq", video_id="A", timestamp_seconds=10,
                     score=0.6, frame_path="", video_path="", full_query_match=True)
    comp = Candidate(frame_id="cq", video_id="A", timestamp_seconds=50,
                     score=0.6, frame_path="", video_path="", full_query_match=False)
    svc._rerank_events([[full], [comp]])
    assert full.final_score > comp.final_score
    assert full.final_score == pytest.approx(0.6 + 0.2, abs=1e-6)
    assert comp.final_score == pytest.approx(0.6, abs=1e-6)


def test_rerank_duplicate_penalty(container):
    from app.domain.models import Candidate

    svc = container.search_service
    svc.settings.rerank_weight_semantic = 1.0
    svc.settings.rerank_weight_full_query = 0.0
    svc.settings.rerank_weight_neighbor = 0.0
    svc.settings.rerank_weight_diversity = 0.0
    svc.settings.rerank_weight_duplicate = 0.5

    e1 = [Candidate(frame_id="a", video_id="A", timestamp_seconds=10, score=0.9, frame_path="", video_path="")]
    e2 = [Candidate(frame_id="b", video_id="A", timestamp_seconds=10.4, score=0.9, frame_path="", video_path="")]
    svc._rerank_events([e1, e2])
    # second event is a near-duplicate (within 1s) of the first -> penalized
    assert e1[0].final_score == pytest.approx(0.9, abs=1e-6)
    assert e2[0].final_score == pytest.approx(0.9 - 0.5, abs=1e-6)


def test_save_payload_roundtrip_full_evidence(container):
    """Regression: the frontend save payload now persists context_frames and
    reason — the service must retain them (no loss of frame metadata)."""
    payload = {
        "query": "a red car",
        "video_id": "v1",
        "filename": "demo.mp4",
        "media_type": "video",
        "timestamp": 16.0,
        "context_start": 8.0,
        "context_end": 24.0,
        "similarity": 0.8,
        "frame_id": "v1_000003",
        "context_text": "evidence block",
        "context_frames": [
            {"frame_id": "v1_000001", "timestamp": 8.0, "timestamp_hms": "00:00:08"},
            {"frame_id": "v1_000003", "timestamp": 16.0, "timestamp_hms": "00:00:16"},
        ],
        "reason": "grounded reason",
    }
    saved = container.context_service.save(payload)
    got = container.context_service.list()[0]
    assert got.id == saved.id
    assert got.timestamp_seconds == pytest.approx(16.0)
    assert got.context_frames == payload["context_frames"]
    assert got.reason == "grounded reason"
    assert got.context_text == "evidence block"
