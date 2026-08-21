"""Image-dataset semantic search test (requires the real SigLIP model).

Run with:  EMBEDDING_BACKEND=siglip pytest tests/test_image_dataset.py -m ml
The test is skipped automatically if torch/transformers or the model weights
are unavailable, so the default offline suite still passes.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.ml
def test_image_semantic_search(tmp_path):
    from app.config import Settings
    from app.exceptions import ModelUnavailableError
    from app.infrastructure.embedding import create_embedding_service

    settings = Settings(
        data_dir=str(tmp_path / "data"),
        chroma_path=str(tmp_path / "data" / "chroma"),
        embedding_backend="siglip",
        _env_file=None,
    )
    try:
        embedding = create_embedding_service(settings)
    except ModelUnavailableError as exc:
        pytest.skip(f"SigLIP unavailable: {exc.message}")

    import chromadb

    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    collection = client.get_or_create_collection(
        name="test_images", metadata={"hnsw:space": "cosine"}
    )

    image_dir = PROJECT_ROOT / "test_images"
    files = sorted(
        p for p in image_dir.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    if not files:
        pytest.skip("test_images/ not populated")

    embs = embedding.embed_images(files)
    collection.upsert(
        ids=[p.stem for p in files],
        embeddings=embs.tolist(),
        metadatas=[{"name": p.name} for p in files],
    )

    def search(query: str, k: int = 2) -> list[str]:
        q = embedding.embed_text([query])[0]
        res = collection.query(query_embeddings=[q.tolist()], n_results=k)
        return [m["name"] for m in res["metadatas"][0]]

    assert search("dog")[0].startswith("dog")
    assert search("a cat")[0].startswith("cat")
    # "car" and "person" are visually distinct; assert a sensible ranking
    top_person = search("a person standing")[0]
    assert top_person.startswith("person")

    shutil.rmtree(tmp_path / "data", ignore_errors=True)
