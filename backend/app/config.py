"""Application configuration.

All configuration is loaded from environment variables or a local ``.env`` file
(see ``.env.example`` at the project root). Nothing is hard-coded: paths, sizes
and model settings are all configurable.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root is the folder containing ``backend/`` and ``frontend/``.
# config.py lives at <root>/backend/app/config.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Application -----------------------------------------------------
    app_env: str = "development"            # development | production
    app_name: str = "AI Media Search"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_url: str = "http://localhost:3000"

    # --- Admin / security ------------------------------------------------
    # Empty in development => destructive endpoints are open locally.
    # In production a token MUST be set or destructive endpoints fail closed.
    admin_token: str = ""
    # Explicitly allow continuing with a mismatched embedding model (dev only,
    # and only when set). Never silently mix vector spaces otherwise.
    allow_model_mismatch: bool = False

    # --- Search limits ---------------------------------------------------
    max_query_length: int = 1000
    max_video_ids: int = 100
    max_media_types: int = 20
    search_history_retention_days: int = 30
    max_search_history_rows: int = 10000

    # --- Storage / uploads ----------------------------------------------
    data_dir: str = "data"
    max_upload_size_gb: float = 10.0
    chunk_size_mb: int = 10
    max_upload_age_hours: int = 24          # abandoned upload cleanup threshold
    max_concurrent_uploads: int = 20        # 0 = unlimited

    # --- Indexing -------------------------------------------------------
    frame_interval_seconds: float = 2.0     # coarse sampling: 1 frame every N seconds
    fine_frame_interval_seconds: float = 0.25
    # phash | dhash | embedding | none
    dedup_method: str = "phash"
    dedup_threshold: float = 0.02           # perceptual: hamming fraction; embedding: cosine sim
    embedding_batch_size: int = 16          # 0 => auto-tune from available resources
    max_concurrent_jobs: int = 1
    job_cancel_timeout_seconds: float = 30.0
    auto_requeue_on_restart: bool = False   # re-queue interrupted jobs on startup
    frame_extraction_timeout_seconds: float = 3600.0

    # --- Image safety (decompression-bomb / oversized-dimension guard) ---
    max_image_dimension: int = 20000        # max single edge in pixels
    max_image_pixels: int = 120_000_000     # max total pixels (~120 MP)

    # --- Search ---------------------------------------------------------
    top_k: int = 50                         # coarse candidate count
    final_results: int = 5
    temporal_group_window_seconds: float = 5.0
    max_results_per_event: int = 1
    fine_search_window_seconds: float = 4.0
    fine_search_max_events: int = 5
    # hard bounds for fine search resource protection
    fine_search_max_videos: int = 3
    fine_search_max_timestamps: int = 10
    fine_search_max_frames: int = 300
    fine_search_max_duration_seconds: float = 120.0
    fine_search_concurrency: int = 2
    context_frames: int = 5                 # "View context" frames around a hit
    # context segment: ±half this window around a match defines start/end
    context_window_seconds: float = 16.0
    # context frame selection: fetch more candidates, dedup, pick representatives
    context_dedup: bool = True              # remove near-duplicate context frames
    context_dedup_threshold: float = 0.02   # phash hamming fraction
    context_max_frames: int = 5             # representative frames returned
    # optional grounded AI summary of the context (needs GEMINI_API_KEY +
    # llm_context_summary). Falls back to the deterministic evidence block.
    llm_context_summary: bool = False
    # ranking score normalization: per_video | none
    ranking_normalization: str = "per_video"

    # --- Query understanding (deterministic, no LLM) --------------------
    # Split connector queries ("X near Y") into component queries and fuse
    # their candidates. Single-component queries are byte-for-byte identical
    # to the old behaviour, so the baseline is preserved.
    query_expansion: bool = True
    query_expansion_connectors: str = (
        "near,with,and,wearing,next to,beside,holding,driving,in front of,on a"
    )
    query_expansion_min_words: int = 2      # drop components shorter than this
    query_expansion_max_components: int = 4
    fusion_method: str = "max"              # max | sum
    # keep the FULL query dominant during fusion: candidates matched by the
    # full-query embedding get this additive score boost so component queries
    # improve recall without overriding precision.
    fusion_full_query_boost: float = 0.05
    query_embedding_cache_size: int = 256

    # --- Deterministic reranking (configurable weighted signals) --------
    # Applied AFTER grouping/fine-search, BEFORE the final sort. Weights are
    # documented, not magic: semantic = normalized vector score; neighbor =
    # mean score of the event's other members (temporal coherence); diversity
    # = bonus for the best event of each video (video spread). Defaults are
    # conservative (baseline preserved for single-video results).
    rerank_deterministic: bool = True
    rerank_weight_semantic: float = 1.0
    # bonus when the representative matched the FULL query embedding
    rerank_weight_full_query: float = 0.1
    rerank_weight_neighbor: float = 0.1
    rerank_weight_diversity: float = 0.05
    # safety-net penalty for same-video near-duplicate events; grouping already
    # merges these, so off by default
    rerank_weight_duplicate: float = 0.0

    # --- Performance budgets (documented targets, not hard limits) --------
    search_p50_latency_budget_ms: float = 1000.0
    search_p95_latency_budget_ms: float = 5000.0
    rerank_max_candidates: int = 20
    llm_max_tokens: int = 64

    # --- Embedding / vector store ---------------------------------------
    # auto: SigLIP when available (fallback to deterministic ONLY outside
    #       production); siglip: require the model; deterministic: baseline.
    embedding_backend: str = "auto"
    siglip_model: str = "google/siglip-base-patch16-224"
    hf_token: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    llm_rerank: bool = False                # optional LLM rerank; disabled without a key
    gemini_timeout_seconds: float = 20.0
    chroma_path: str = "data/chroma"
    chroma_collection: str = "media_embeddings"

    # --- FFmpeg ---------------------------------------------------------
    ffmpeg_path: str = ""
    ffprobe_path: str = ""

    # --- CORS -----------------------------------------------------------
    cors_origins: str = ""                  # comma separated extra origins

    # -------------------------------------------------------------------
    @property
    def production(self) -> bool:
        return self.app_env.lower() == "production"

    def resolve(self, p: str) -> Path:
        path = Path(p)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    @property
    def data_dir_path(self) -> Path:
        return self.resolve(self.data_dir)

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir_path / "uploads"

    @property
    def media_dir(self) -> Path:
        return self.data_dir_path / "media"

    @property
    def frames_dir(self) -> Path:
        return self.data_dir_path / "frames"

    @property
    def thumbnails_dir(self) -> Path:
        return self.data_dir_path / "thumbnails"

    @property
    def chroma_dir(self) -> Path:
        return self.resolve(self.chroma_path)

    @property
    def logs_dir(self) -> Path:
        return self.data_dir_path / "logs"

    @property
    def temp_dir(self) -> Path:
        return self.data_dir_path / "temp"

    @property
    def database_dir(self) -> Path:
        return self.data_dir_path / "database"

    @property
    def db_path(self) -> Path:
        return self.database_dir / "app.db"

    @property
    def max_upload_size_bytes(self) -> int:
        return int(self.max_upload_size_gb * 1024 ** 3)

    @property
    def chunk_size_bytes(self) -> int:
        return self.chunk_size_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        for d in (
            self.data_dir_path,
            self.uploads_dir,
            self.media_dir,
            self.frames_dir,
            self.thumbnails_dir,
            self.chroma_dir,
            self.logs_dir,
            self.temp_dir,
            self.database_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
