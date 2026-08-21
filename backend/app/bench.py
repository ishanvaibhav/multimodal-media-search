"""Benchmarking tooling.

Measures embedding throughput, search latency and synthetic scale behaviour.
The ``synthetic`` command is clearly a *metadata/vector* benchmark (no real
media) — it exercises the same SQLite + Chroma + search code paths at
100 / 1,000 / 10,000 videos without requiring real video files.

Usage (from backend/):

    python -m app.bench embed --frames 100 1000 10000 [--embedding-backend siglip]
    python -m app.bench search --queries 20 [--data-dir ../data]
    python -m app.bench synthetic --videos 100 1000 10000 --frames-per-video 10
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time
import uuid
from pathlib import Path


def _synthetic_frames(n: int, tmpdir: Path) -> list[Path]:
    import numpy as np
    from PIL import Image

    paths = []
    for i in range(n):
        hue = (i * 37) % 256
        arr = np.zeros((64, 64, 3), dtype=np.uint8)
        arr[:, :, 0] = hue
        arr[:, :, 1] = (i * 17) % 256
        arr[:, :, 2] = (i * 29) % 256
        p = tmpdir / f"f{i:06d}.jpg"
        Image.fromarray(arr, "RGB").save(p, "JPEG", quality=80)
        paths.append(p)
    return paths


def bench_embed(settings, counts: list[int]) -> dict:
    from app.infrastructure.embedding import create_embedding_service

    svc = create_embedding_service(settings)
    report = {"backend": svc.name, "dim": svc.dim, "device": getattr(svc, "device", "cpu"), "results": []}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for n in counts:
            frames = _synthetic_frames(n, tmp)
            t0 = time.perf_counter()
            svc.embed_images(frames)
            dt = time.perf_counter() - t0
            report["results"].append({
                "frames": n,
                "seconds": round(dt, 3),
                "fps": round(n / dt, 2) if dt else 0.0,
            })
            print(f"  {n:>6} frames: {dt:6.2f}s  ({n / dt:8.1f} frames/s)")
    return report


def bench_search(settings, queries: int) -> dict:
    from app.container import build_container

    container = build_container(settings)
    svc = container.search_service
    n = container.vectorstore.count()
    times = []
    for i in range(queries):
        t0 = time.perf_counter()
        svc.search(f"test query {i}", {"mode": "fast", "fine_search": False})
        times.append(time.perf_counter() - t0)
    times.sort()
    return {
        "vectors": n,
        "queries": queries,
        "mean_ms": round(sum(times) / len(times) * 1000, 2),
        "p50_ms": round(times[len(times) // 2] * 1000, 2),
        "p95_ms": round(times[int(len(times) * 0.95)] * 1000, 2),
    }


def bench_synthetic(settings, video_counts: list[int], frames_per_video: int = 10) -> dict:
    """Synthetic scale benchmark: N videos x F frame-vectors, no real media.

    Exercises the real metadata (SQLite) and vector (Chroma) code paths with
    deterministic embeddings. Clearly labeled as synthetic.
    """
    import numpy as np

    from app.container import build_container

    report = {"label": "synthetic (no real media)", "frames_per_video": frames_per_video, "results": []}
    for nv in video_counts:
        # fresh isolated container per size
        import shutil

        d = tempfile.mkdtemp(prefix="bench_synth_")
        from app.config import Settings as _S

        s = _S(
            data_dir=d, chroma_path=f"{d}/chroma",
            embedding_backend="deterministic", _env_file=None,
        )
        c = build_container(s)
        dim = c.embedding.dim
        t0 = time.perf_counter()
        for v in range(nv):
            vid = uuid.uuid4().hex
            from app.domain.models import Video
            from app.utils import now_iso

            c.video_repo.insert(Video(
                video_id=vid, filename=f"v{vid[:8]}.mp4", original_filename=f"video_{v}.mp4",
                path=f"media/{vid}.mp4", size_bytes=1000, duration_seconds=60.0,
                status="ready", uploaded_at=now_iso(), created_at=now_iso(), updated_at=now_iso(),
            ))
            ids = [f"{vid}_{i:04d}" for i in range(frames_per_video)]
            embs = np.random.default_rng(v).random((frames_per_video, dim)).astype("float32")
            embs /= np.linalg.norm(embs, axis=1, keepdims=True)
            metas = [{"video_id": vid, "timestamp": i * 2.0, "frame_id": fid}
                     for i, fid in enumerate(ids)]
            c.vectorstore.upsert(ids, embs, metas)
        insert_s = time.perf_counter() - t0
        total_vectors = nv * frames_per_video

        # search latency
        q = c.embedding.embed_text(["a synthetic query"])[0]
        ts = []
        for _ in range(10):
            t = time.perf_counter()
            c.vectorstore.query(q, top_k=10)
            ts.append(time.perf_counter() - t)
        ts.sort()

        # media-list keyset pagination latency
        t = time.perf_counter()
        page = c.video_repo.list_keyset(None, limit=100)
        list_ms = (time.perf_counter() - t) * 1000

        report["results"].append({
            "videos": nv,
            "vectors": total_vectors,
            "insert_seconds": round(insert_s, 2),
            "videos_per_sec": round(nv / insert_s, 1) if insert_s else 0,
            "vectors_per_sec": round(total_vectors / insert_s, 1) if insert_s else 0,
            "search_p50_ms": round(ts[len(ts) // 2] * 1000, 2),
            "search_p95_ms": round(ts[int(len(ts) * 0.95)] * 1000, 2),
            "media_list_page_ms": round(list_ms, 2),
            "page_rows": len(page),
        })
        print(f"  {nv:>6} videos / {total_vectors:>8} vectors: "
              f"insert {insert_s:6.2f}s, search p50 {ts[len(ts)//2]*1000:7.2f}ms, "
              f"page {list_ms:5.2f}ms")
        shutil.rmtree(d, ignore_errors=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.bench")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("embed")
    p.add_argument("--frames", nargs="+", type=int, default=[100, 1000, 10000])
    p.add_argument("--embedding-backend", default="auto")
    p.add_argument("--data-dir", default="../data")

    p = sub.add_parser("search")
    p.add_argument("--queries", type=int, default=20)
    p.add_argument("--data-dir", default="../data")
    p.add_argument("--embedding-backend", default="auto")

    p = sub.add_parser("synthetic")
    p.add_argument("--videos", nargs="+", type=int, default=[100, 1000, 10000])
    p.add_argument("--frames-per-video", type=int, default=10)
    p.add_argument("--data-dir", default="../data")
    p.add_argument("--embedding-backend", default="deterministic")

    args = parser.parse_args(argv)

    from app.config import Settings

    settings = Settings(
        data_dir=args.data_dir,
        chroma_path=f"{args.data_dir}/chroma",
        embedding_backend=args.embedding_backend,
        _env_file=None,
    )

    if args.command == "embed":
        print("Embedding benchmark:")
        report = bench_embed(settings, args.frames)
    elif args.command == "synthetic":
        print("Synthetic scale benchmark (metadata + vectors, no real media):")
        report = bench_synthetic(settings, args.videos, args.frames_per_video)
    else:
        print("Search benchmark:")
        report = bench_search(settings, args.queries)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
