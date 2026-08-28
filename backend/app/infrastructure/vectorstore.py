"""Vector store façade.

Business logic depends on the ``VectorStoreBackend`` protocol (see
vector_backend.py). ``VectorStore`` is the façade services call; it stamps
model-traceability metadata onto every vector, validates model compatibility
against the existing index, and delegates storage to the configured backend
(ChromaDB today; Qdrant/Weaviate/pgvector later).
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from ..config import Settings
from ..domain.models import Candidate
from ..exceptions import VectorStoreError
from ..logging_config import get_logger
from .vector_backend import ChromaVectorBackend, VectorStoreBackend

log = get_logger(__name__)


class VectorStore:
    def __init__(
        self,
        settings: Settings,
        embedding_dim: int,
        embedding_meta: dict | None = None,
        backend: VectorStoreBackend | None = None,
    ):
        self.settings = settings
        self.embedding_dim = embedding_dim
        self.embedding_meta = dict(embedding_meta or {})
        self._lock = threading.RLock()
        self.backend = backend or ChromaVectorBackend(settings, embedding_dim)
        self.model_mismatch = False
        self._verify_configuration()

    # ------------------------------------------------------------------
    # model-compatibility verification (kept on the façade; backend-agnostic)
    # ------------------------------------------------------------------
    def _sample_existing(self):
        collection = getattr(self.backend, "_collection", None)
        if collection is None:
            return None
        try:
            return collection.get(limit=1, include=["embeddings", "metadatas"])
        except Exception:
            return None

    def _verify_configuration(self) -> None:
        existing = self._sample_existing()
        if not existing or existing.get("embeddings") is None or not len(existing["embeddings"]):
            return

        shape = np.asarray(existing["embeddings"]).shape
        metas = existing.get("metadatas") or [{}]
        stored = dict(metas[0] or {})

        mismatches = []
        if shape[-1] != self.embedding_dim:
            mismatches.append(f"dimension {shape[-1]} != {self.embedding_dim}")
        for field, current in (
            ("embedding_model", self.embedding_meta.get("embedding_model")),
            ("model_version", self.embedding_meta.get("model_version")),
            ("preprocessing_version", self.embedding_meta.get("preprocessing_version")),
            ("indexing_version", self.embedding_meta.get("indexing_version")),
        ):
            stored_val = stored.get(field)
            if stored_val and current and stored_val != current:
                mismatches.append(f"{field} {stored_val!r} != {current!r}")

        if not mismatches:
            return

        message = (
            f"Existing collection '{self.settings.chroma_collection}' was indexed with a "
            f"different embedding configuration ({'; '.join(mismatches)}). Reindex "
            f"(or DELETE /api/admin/data) before searching."
        )
        allow_override = bool(getattr(self.settings, "allow_model_mismatch", False))
        if self.settings.production or not allow_override:
            raise VectorStoreError(message)
        self.model_mismatch = True
        log.warning("ALLOW_MODEL_MISMATCH is set; continuing with a mismatched model. %s", message)

    # ------------------------------------------------------------------
    def upsert(
        self,
        ids: list[str],
        embeddings: np.ndarray,
        metadatas: list[dict],
        documents: list[str] | None = None,
    ) -> None:
        if not ids:
            return
        # stamp model-traceability metadata onto every vector
        stamped = [{**self.embedding_meta, **m} for m in metadatas]
        with self._lock:
            self.backend.upsert(ids, embeddings, stamped, documents)

    def query(self, embedding: np.ndarray, top_k: int, where: Optional[dict] = None) -> list[Candidate]:
        with self._lock:
            return self.backend.query(embedding, top_k, where)

    def delete_by_video(self, video_id: str) -> int:
        with self._lock:
            return self.backend.delete_by_video(video_id)

    def delete_all(self) -> None:
        with self._lock:
            self.backend.delete_all()

    def count(self) -> int:
        return self.backend.count()

    def all_ids_for_video(self, video_id: str) -> set[str]:
        return self.backend.all_ids_for_video(video_id)

    def all_ids(self) -> set[str]:
        return self.backend.all_ids()

    def healthy(self) -> bool:
        return self.backend.healthy()

    def close(self) -> None:
        self.backend.close()

    # internal access for the consistency checker (backend-specific sampling)
    @property
    def _collection(self):
        return getattr(self.backend, "_collection", None)
