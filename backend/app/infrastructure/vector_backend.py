"""Vector storage abstraction.

Business logic (search/indexing) depends on the ``VectorStoreBackend`` protocol
— never on Chroma-specific calls. ChromaDB is the current implementation
(``ChromaVectorBackend``); migrating to Qdrant/Weaviate/Postgres-pgvector later
means implementing the same protocol, not rewriting search logic.

``VectorStore`` remains the façade that the services call; it delegates all
Chroma-specific behaviour to ``ChromaVectorBackend``.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import numpy as np

from ..domain.models import Candidate


@runtime_checkable
class VectorStoreBackend(Protocol):
    name: str

    def upsert(self, ids: list[str], embeddings: np.ndarray,
               metadatas: list[dict], documents: list[str] | None = None) -> None: ...
    def query(self, embedding: np.ndarray, top_k: int,
              where: Optional[dict] = None) -> list[Candidate]: ...
    def delete_by_video(self, video_id: str) -> int: ...
    def delete_all(self) -> None: ...
    def count(self) -> int: ...
    def all_ids(self) -> set[str]: ...
    def all_ids_for_video(self, video_id: str) -> set[str]: ...
    def healthy(self) -> bool: ...
    def close(self) -> None: ...


class ChromaVectorBackend:
    """ChromaDB persistent implementation (cosine space)."""

    name = "chromadb"

    def __init__(self, settings, embedding_dim: int):
        import threading

        import chromadb

        self.settings = settings
        self.embedding_dim = embedding_dim
        self._lock = threading.RLock()
        self._client = chromadb.PersistentClient(path=str(settings.chroma_dir))
        self._collection = self._client.get_or_create_collection(
            name=settings.chroma_collection,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, ids, embeddings, metadatas, documents=None):
        from ..exceptions import VectorStoreError

        emb = np.asarray(embeddings, dtype=np.float32)
        if emb.ndim == 1:
            emb = emb[None, :]
        if emb.shape[1] != self.embedding_dim:
            raise VectorStoreError(
                f"embedding dimension mismatch: {emb.shape[1]} != {self.embedding_dim}"
            )
        with self._lock:
            try:
                self._collection.upsert(
                    ids=ids, embeddings=emb.tolist(), metadatas=metadatas,
                    documents=documents or [""] * len(ids),
                )
            except Exception as exc:
                raise VectorStoreError(f"chroma upsert failed: {exc}") from exc

    def query(self, embedding, top_k, where=None):
        from ..exceptions import VectorStoreError

        emb = np.asarray(embedding, dtype=np.float32)
        with self._lock:
            try:
                res = self._collection.query(
                    query_embeddings=[emb.tolist()], n_results=top_k, where=where or None,
                )
            except Exception as exc:
                raise VectorStoreError(f"chroma query failed: {exc}") from exc
        candidates = []
        ids = res.get("ids") or [[]]
        dists = res.get("distances") or [[]]
        metas = res.get("metadatas") or [[]]
        for hit_id, dist, meta in zip(ids[0], dists[0], metas[0]):
            meta = meta or {}
            candidates.append(Candidate(
                frame_id=hit_id,
                video_id=str(meta.get("video_id", "")),
                timestamp_seconds=float(meta.get("timestamp", 0.0)),
                score=float(1.0 - dist),
                frame_path=str(meta.get("frame_path", "")),
                video_path=str(meta.get("video_path", "")),
                uploaded_at=meta.get("uploaded_at"),
                duration=meta.get("duration"),
                metadata=dict(meta),
            ))
        return candidates

    def delete_by_video(self, video_id):
        from ..exceptions import VectorStoreError

        with self._lock:
            try:
                before = self._collection.count()
                self._collection.delete(where={"video_id": video_id})
                return max(0, before - self._collection.count())
            except Exception as exc:
                raise VectorStoreError(f"chroma delete failed: {exc}") from exc

    def delete_all(self):
        from ..exceptions import VectorStoreError

        with self._lock:
            try:
                self._client.delete_collection(self.settings.chroma_collection)
                self._collection = self._client.get_or_create_collection(
                    name=self.settings.chroma_collection,
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception as exc:
                raise VectorStoreError(f"chroma reset failed: {exc}") from exc

    def count(self):
        with self._lock:
            try:
                return int(self._collection.count())
            except Exception:
                return -1

    def all_ids(self):
        with self._lock:
            try:
                return set(self._collection.get(include=[])["ids"])
            except Exception:
                return set()

    def all_ids_for_video(self, video_id):
        with self._lock:
            try:
                res = self._collection.get(where={"video_id": video_id}, include=[])
                return set(res.get("ids") or [])
            except Exception:
                return set()

    def healthy(self):
        return self.count() >= 0

    def close(self):
        try:
            closer = getattr(self._client, "clear_system_cache", None)
            if closer is not None:
                closer()
        except Exception:
            pass
