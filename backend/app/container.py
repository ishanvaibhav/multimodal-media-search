"""Composition root: wires configuration and services together.

Everything the API layer needs is reachable from this single container, which
makes the dependency graph explicit and easy to swap (e.g. replacing SQLite
with Postgres or ChromaDB with Qdrant later).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .application.consistency_service import ConsistencyService
from .application.indexing_service import IndexingService
from .application.job_service import JobService
from .application.media_service import MediaService
from .application.recovery_service import RecoveryService
from .application.search_service import SearchService
from .application.upload_service import UploadService
from .config import Settings
from .infrastructure.coordinator import (
    ChunkLocks,
    KeyedLocks,
    MaintenanceGate,
    UploadLimiter,
    VideoCoordinator,
)
from .infrastructure.database import Database
from .infrastructure.embedding import EmbeddingService, create_embedding_service
from .infrastructure.ffmpeg import FFmpegService
from .infrastructure.repositories import (
    FeedbackRepository,
    FineCacheRepository,
    FrameRepository,
    JobRepository,
    SearchHistoryRepository,
    UploadRepository,
    VideoRepository,
)
from .infrastructure.reranker import LLMReranker
from .infrastructure.storage import StorageService
from .infrastructure.vectorstore import VectorStore
from .workers.indexing_worker import IndexingWorker


@dataclass
class Container:
    settings: Settings
    database: Database
    storage: StorageService
    ffmpeg: FFmpegService
    embedding: EmbeddingService
    vectorstore: VectorStore
    reranker: LLMReranker
    coordinator: VideoCoordinator
    gate: MaintenanceGate
    chunk_locks: ChunkLocks
    limiter: UploadLimiter
    fine_cache_locks: KeyedLocks

    video_repo: VideoRepository
    frame_repo: FrameRepository
    upload_repo: UploadRepository
    job_repo: JobRepository
    history_repo: SearchHistoryRepository
    feedback_repo: FeedbackRepository
    fine_cache_repo: FineCacheRepository

    job_service: JobService
    upload_service: UploadService
    indexing_service: IndexingService
    media_service: MediaService
    search_service: SearchService
    recovery_service: RecoveryService
    consistency_service: ConsistencyService

    worker: IndexingWorker = field(default=None)  # type: ignore[assignment]

    @property
    def semantic_search(self) -> bool:
        return self.embedding.semantic


def build_container(settings: Settings) -> Container:
    settings.ensure_dirs()

    database = Database(settings.db_path)
    storage = StorageService(settings)
    ffmpeg = FFmpegService(settings)
    embedding = create_embedding_service(settings)
    vectorstore = VectorStore(
        settings, embedding_dim=embedding.dim, embedding_meta=embedding.metadata()
    )
    reranker = LLMReranker(settings)
    coordinator = VideoCoordinator()
    gate = MaintenanceGate()
    chunk_locks = ChunkLocks()
    limiter = UploadLimiter(settings.max_concurrent_uploads)
    fine_cache_locks = KeyedLocks()

    video_repo = VideoRepository(database)
    frame_repo = FrameRepository(database)
    upload_repo = UploadRepository(database)
    job_repo = JobRepository(database)
    history_repo = SearchHistoryRepository(database)
    feedback_repo = FeedbackRepository(database)
    fine_cache_repo = FineCacheRepository(database)

    job_service = JobService(job_repo, video_repo, gate)
    upload_service = UploadService(
        settings, upload_repo, video_repo, storage, ffmpeg, job_service,
        chunk_locks=chunk_locks, limiter=limiter, gate=gate,
    )
    indexing_service = IndexingService(
        settings, video_repo, frame_repo, job_repo,
        storage, ffmpeg, embedding, vectorstore,
    )

    container = Container(
        settings=settings,
        database=database,
        storage=storage,
        ffmpeg=ffmpeg,
        embedding=embedding,
        vectorstore=vectorstore,
        reranker=reranker,
        coordinator=coordinator,
        gate=gate,
        chunk_locks=chunk_locks,
        limiter=limiter,
        fine_cache_locks=fine_cache_locks,
        video_repo=video_repo,
        frame_repo=frame_repo,
        upload_repo=upload_repo,
        job_repo=job_repo,
        history_repo=history_repo,
        feedback_repo=feedback_repo,
        fine_cache_repo=fine_cache_repo,
        job_service=job_service,
        upload_service=upload_service,
        indexing_service=indexing_service,
        media_service=None,  # type: ignore[assignment]
        search_service=None,  # type: ignore[assignment]
        recovery_service=None,  # type: ignore[assignment]
        consistency_service=None,  # type: ignore[assignment]
        worker=None,
    )
    container.worker = IndexingWorker(container)

    container.media_service = MediaService(
        video_repo, frame_repo, upload_repo, job_repo,
        storage, vectorstore, job_service, coordinator, container.worker, settings,
        fine_cache_repo,
    )
    container.search_service = SearchService(
        settings, video_repo, frame_repo, history_repo, feedback_repo,
        storage, ffmpeg, embedding, vectorstore, reranker, fine_cache_repo,
        gate=gate, fine_cache_locks=fine_cache_locks,
    )
    container.recovery_service = RecoveryService(container)
    container.consistency_service = ConsistencyService(container)
    _record_model_info(database, embedding)
    return container


def _record_model_info(database: Database, embedding: EmbeddingService) -> None:
    """Record the active embedding configuration in the canonical model_info
    registry (used for startup validation, diagnostics and reindex decisions)."""
    from .utils import now_iso
    from .versioning import INDEXING_VERSION, PREPROCESSING_VERSION

    meta = embedding.metadata()
    database.execute(
        "UPDATE model_info SET is_active = 0 WHERE is_active = 1"
    )
    database.execute(
        """
        INSERT INTO model_info (embedding_model, model_version, embedding_dim,
            preprocessing_ver, indexing_version, is_active, created_at)
        VALUES (?,?,?,?,?,1,?)
        """,
        (
            meta.get("embedding_model", embedding.name),
            meta.get("model_version") or "",
            embedding.dim,
            meta.get("preprocessing_version", PREPROCESSING_VERSION),
            meta.get("indexing_version", INDEXING_VERSION),
            now_iso(),
        ),
    )
