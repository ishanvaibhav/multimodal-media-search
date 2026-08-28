"""Offline retrieval-evaluation framework.

Runs a golden dataset of (query, expected video+timestamp) pairs against an
already-indexed data directory and reports Recall@K, Precision@K, MRR,
video-level recall and temporal localization accuracy.

Usage (from backend/):

    python -m app.eval run --dataset evaluation/golden_dataset.json \
        --data-dir ../data [--embedding-backend siglip]

Exit code is non-zero if any metric falls below its configured threshold
(usable as a search-quality regression gate).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def load_dataset(path: Path) -> dict:
    data = json.loads(path.read_text())
    queries = data.get("queries") or data.get("items") or []
    if not queries:
        raise SystemExit(f"dataset {path} has no 'queries'")
    return data


def run_eval(dataset: dict, settings, k: int = 5) -> dict:
    from app.container import build_container

    container = build_container(settings)
    service = container.search_service

    queries = dataset["queries"]
    tolerance = float(dataset.get("tolerance_seconds", 3.0))

    # resolve video_name -> video_id for the current data dir
    name_to_id: dict[str, str] = {}
    for v in container.video_repo.list(limit=100000):
        name_to_id[v.original_filename] = v.video_id

    hits_at = {kk: 0 for kk in (1, 5, 10)}
    precision_hits = 0
    rr_sum = 0.0
    temporal_ok = 0
    video_recall_ok = 0
<<<<<<< HEAD
    iou_sum = 0.0
    overlap_queries = 0
=======
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
    total_expected = 0
    n = 0
    per_query = []

    for item in queries:
        query = item["query"]
        vid = name_to_id.get(item.get("video_name", ""))
        if vid is None:
            per_query.append({"query": query, "error": "video not found", "skip": True})
            continue
        expected_ts = {round(float(t), 2) for t in item.get("expected", [])}
        if not expected_ts:
            continue

        started = time.time()
        result = service.search(query, {"mode": "fast", "fine_search": False, "final_results": k})
        latency_ms = int((time.time() - started) * 1000)
        preds = result["results"]

        retrieved = [(r["video_id"], round(float(r["timestamp"]), 2)) for r in preds[:k]]
        hit_ts = [t for (v, t) in retrieved if v == vid and t in expected_ts]
        hit_video = [v for (v, _) in retrieved if v == vid]

        for kk in (1, 5, 10):
            if any(t in expected_ts and v == vid for (v, t) in retrieved[:kk]):
                hits_at[kk] += 1
        precision_hits += len(hit_ts)
        total_expected += len(expected_ts)

        # MRR: first rank at which ANY expected item appears
        for rank, (v, t) in enumerate(retrieved, start=1):
            if v == vid and t in expected_ts:
                rr_sum += 1.0 / rank
                break

        # temporal localization: closest predicted ts to any expected ts
        best_err = None
        for (v, t) in retrieved:
            if v != vid:
                continue
            for e in expected_ts:
                err = abs(t - e)
                best_err = err if best_err is None else min(best_err, err)
        if best_err is not None and best_err <= tolerance:
            temporal_ok += 1
        if hit_video:
            video_recall_ok += 1

<<<<<<< HEAD
        # temporal IoU: predicted window [t-τ, t+τ] vs expected [e-τ, e+τ]
        best_iou = 0.0
        for (v, t) in retrieved:
            if v != vid:
                continue
            for e in expected_ts:
                overlap = max(0.0, 2 * tolerance - abs(t - e))
                if overlap > 0:
                    best_iou = max(best_iou, overlap / (4 * tolerance - overlap))
        iou_sum += best_iou
        if best_iou > 0:
            overlap_queries += 1

=======
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
        n += 1
        per_query.append({
            "query": query,
            "latency_ms": latency_ms,
            "retrieved": retrieved,
            "expected": sorted(expected_ts),
            "temporal_error_s": best_err,
        })

    if n == 0:
        return {"error": "no evaluable queries"}

    def rate(num: int) -> float:
        return round(num / n, 4) if n else 0.0

    return {
        "queries": n,
        "recall_at_1": rate(hits_at[1]),
        "recall_at_5": rate(hits_at[5]),
        "recall_at_10": rate(hits_at[10]),
        "precision_at_k": round(precision_hits / (n * k), 4) if n else 0.0,
        "mrr": round(rr_sum / n, 4) if n else 0.0,
        "video_recall": rate(video_recall_ok),
        "temporal_accuracy": rate(temporal_ok),
<<<<<<< HEAD
        "temporal_iou": round(iou_sum / n, 4) if n else 0.0,
        "segment_recall": rate(overlap_queries),
=======
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
        "tolerance_seconds": tolerance,
        "per_query": per_query,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.eval")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run")
    p.add_argument("--dataset", required=True)
    p.add_argument("--data-dir", default="../data")
    p.add_argument("--embedding-backend", default="auto")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--min-recall-1", type=float, default=0.0)
    p.add_argument("--min-recall-5", type=float, default=0.0)
    p.add_argument("--min-mrr", type=float, default=0.0)
    p.add_argument("--min-temporal", type=float, default=0.0)

    args = parser.parse_args(argv)

    from app.config import Settings

    settings = Settings(
        data_dir=args.data_dir,
        chroma_path=f"{args.data_dir}/chroma",
        embedding_backend=args.embedding_backend,
        _env_file=None,
    )
    dataset = load_dataset(Path(args.dataset))
    report = run_eval(dataset, settings, k=args.k)
    print(json.dumps(report, indent=2))

    # regression gate: non-zero exit if any metric is below threshold
    if "error" in report:
        return 1
    failures = []
    if report["recall_at_1"] < args.min_recall_1:
        failures.append(f"recall@1 {report['recall_at_1']} < {args.min_recall_1}")
    if report["recall_at_5"] < args.min_recall_5:
        failures.append(f"recall@5 {report['recall_at_5']} < {args.min_recall_5}")
    if report["mrr"] < args.min_mrr:
        failures.append(f"mrr {report['mrr']} < {args.min_mrr}")
    if report["temporal_accuracy"] < args.min_temporal:
        failures.append(f"temporal {report['temporal_accuracy']} < {args.min_temporal}")
    if failures:
        print("EVALUATION REGRESSION: " + "; ".join(failures), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
