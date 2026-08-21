"""Developer CLI utilities.

Usage (from the backend/ directory):

    # Index a small image dataset into its own Chroma collection
    python -m app.cli index-images --dir ../test_images

    # Search the indexed images
    python -m app.cli search-images "dog"

    # Clear indexed images
    python -m app.cli clear-images

    # Generate a synthetic test video (requires FFmpeg)
    python -m app.cli make-test-video --out ../data/test/sample.mp4 --seconds 60

    # Verify FFmpeg / model availability
    python -m app.cli doctor
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from pathlib import Path

from .config import get_settings
from .infrastructure.embedding import create_embedding_service
from .logging_config import configure_logging, get_logger
from .utils import format_hms, human_size

log = get_logger(__name__)

IMAGE_COLLECTION = "image_embeddings"


def _embedding_service():
    settings = get_settings()
    configure_logging("INFO")
    return settings, create_embedding_service(settings)


def _image_collection(settings):
    import chromadb

    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    return client.get_or_create_collection(
        name=IMAGE_COLLECTION, metadata={"hnsw:space": "cosine"}
    )


def index_images(directory: Path) -> int:
    settings, embedding = _embedding_service()
    directory = directory.resolve()
    if not directory.exists():
        print(f"directory not found: {directory}")
        return 1
    files = sorted(
        p for p in directory.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
    )
    if not files:
        print(f"no images found in {directory}")
        return 1

    collection = _image_collection(settings)
    print(f"Embedding {len(files)} images with {embedding.name} (dim={embedding.dim})...")
    ids, metas = [], []
    for path in files:
        ids.append(f"img_{uuid.uuid4().hex[:12]}")
        metas.append({"name": path.name, "path": str(path)})
    embeddings = embedding.embed_images(files)
    collection.upsert(ids=ids, embeddings=embeddings.tolist(), metadatas=metas)
    print(f"Indexed {len(files)} images into collection '{IMAGE_COLLECTION}'")
    return 0


def search_images(query: str, top_k: int = 5) -> int:
    settings, embedding = _embedding_service()
    collection = _image_collection(settings)
    q = embedding.embed_text([query])[0]
    res = collection.query(query_embeddings=[q.tolist()], n_results=min(top_k, collection.count()))
    print(f"Results for '{query}':")
    for i, (hit_id, dist, meta) in enumerate(
        zip(res["ids"][0], res["distances"][0], res["metadatas"][0])
    ):
        print(f"  {i + 1}. {meta['name']:<20s} similarity={1 - dist:.4f}")
    return 0


def clear_images() -> int:
    settings, _ = _embedding_service()
    import chromadb

    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    try:
        client.delete_collection(IMAGE_COLLECTION)
    except Exception:
        pass
    print(f"Cleared collection '{IMAGE_COLLECTION}'")
    return 0


def make_test_video(out: Path, seconds: int, size: str = "640x360", fps: int = 25) -> int:
    settings, _ = _embedding_service()
    from .infrastructure.ffmpeg import FFmpegService

    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = FFmpegService(settings)
    cmd = [
        str(ffmpeg.resolve_ffmpeg()), "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", (
            f"testsrc2=size={size}:rate={fps}:duration={seconds}"
        ),
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-shortest", "-y", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        print("ffmpeg error:", proc.stderr[-500:])
        return 1
    print(f"Wrote {out} ({human_size(out.stat().st_size)}, {seconds}s)")
    return 0


def doctor() -> int:
    settings, embedding = _embedding_service()
    from .infrastructure.ffmpeg import FFmpegService

    ffmpeg = FFmpegService(settings)
    print("AI Media Search — environment check")
    print(f"  Python        : {sys.version.split()[0]}")
    print(f"  FFmpeg        : {ffmpeg.ffmpeg_version()}")
    print(f"  FFprobe       : {ffmpeg.resolve_ffprobe() or 'unavailable'}")
    print(f"  Embedding     : {embedding.name} (dim={embedding.dim}, device={getattr(embedding, 'device', 'cpu')})")
    print(f"  Data dir      : {settings.data_dir_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index-images")
    p.add_argument("--dir", default="../test_images")
    p.add_argument("--collection", default=IMAGE_COLLECTION)

    p = sub.add_parser("search-images")
    p.add_argument("query")
    p.add_argument("--top-k", type=int, default=5)

    sub.add_parser("clear-images")

    p = sub.add_parser("make-test-video")
    p.add_argument("--out", default="../data/test/sample.mp4")
    p.add_argument("--seconds", type=int, default=60)
    p.add_argument("--size", default="640x360")

    sub.add_parser("doctor")

    args = parser.parse_args(argv)
    if args.command == "index-images":
        return index_images(Path(args.dir))
    if args.command == "search-images":
        return search_images(args.query, args.top_k)
    if args.command == "clear-images":
        return clear_images()
    if args.command == "make-test-video":
        return make_test_video(Path(args.out), args.seconds, args.size)
    if args.command == "doctor":
        return doctor()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
