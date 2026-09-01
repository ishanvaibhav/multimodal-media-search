"""Application configuration.

Single source of truth for every environment-driven knob, per the master
plan (§57). Values load from the process environment and, outside
production, from a local ``.env`` file.

Rule: nothing in the codebase may read ``os.environ`` directly — always go
through ``get_settings()`` so configuration is typed, documented and
validated at startup (fail fast, never silently degrade — plan §70/R8).
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]

AppEnv = Literal["development", "test", "production"]
AuthMode = Literal["firebase", "dev"]
StorageBackend = Literal["local", "s3"]


class Settings(BaseSettings):
    """Typed application settings.

    Every field maps 1:1 to an environment variable of the same name.
    """

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # --- Core ---------------------------------------------------------------
    APP_ENV: AppEnv = "development"
    API_VERSION: str = "v1"
    LOG_LEVEL: str = "INFO"
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000
    FRONTEND_URL: str = "http://localhost:3000"
    CORS_ORIGINS: str = "http://localhost:3000"

    # --- Data layer ---------------------------------------------------------
    # SQLite is for local development/testing only (plan §4); production must
    # use PostgreSQL, e.g. postgresql+psycopg://user:pass@host:5432/aimedia
    DATABASE_URL: str = f"sqlite:///{BACKEND_ROOT / 'data' / 'app.db'}"
    DB_SCHEMA_VERSION: int = 1

    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Storage ------------------------------------------------------------
    STORAGE_BACKEND: StorageBackend = "local"
    STORAGE_PATH: str = str(BACKEND_ROOT / "data" / "storage")
    S3_ENDPOINT: str | None = None
    S3_BUCKET: str | None = None
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None

    # --- Vector search ------------------------------------------------------
    CHROMA_HOST: str = "localhost"
    CHROMA_PORT: int = 8100
    CHROMA_PATH: str = str(BACKEND_ROOT / "data" / "chroma")
    CHROMA_COLLECTION: str = "media_frames"

    # --- ML / indexing (reserved for Phases 5–8) ---------------------------
    EMBEDDING_MODEL: str = "google/siglip-base-patch16-224"
    EMBEDDING_BACKEND: Literal["siglip", "auto", "deterministic"] = "auto"
    EMBEDDING_DIMENSION: int = 768
    PREPROCESSING_VERSION: int = 1
    INDEX_VERSION: int = 1
    EMBEDDING_VERSION: int = 1
    COARSE_FRAME_INTERVAL: float = 2.0
    FINE_FRAME_INTERVAL: float = 0.5

    # --- Uploads (reserved for Phase 4) -------------------------------------
    MAX_UPLOAD_SIZE_GB: float = 4.0
    UPLOAD_CHUNK_SIZE_MB: int = 8
    MAX_UPLOAD_AGE_HOURS: int = 24
    MAX_CONCURRENT_UPLOADS: int = 4

    # --- Jobs (reserved for Phase 5+) ---------------------------------------
    MAX_CONCURRENT_JOBS: int = 2
    JOB_CANCEL_TIMEOUT_SECONDS: float = 30.0

    # --- Auth ---------------------------------------------------------------
    # firebase  → verify Google-issued ID tokens (production behaviour)
    # dev       → accept unsigned local dev tokens (NEVER allowed when
    #             APP_ENV=production; startup aborts otherwise)
    AUTH_MODE: AuthMode = "dev"
    FIREBASE_PROJECT_ID: str | None = None
    FIREBASE_SERVICE_ACCOUNT: str | None = None  # path to service-account JSON
    # Email that is pre-provisioned as the first ADMIN. Solves the
    # chicken-and-egg bootstrap problem (first user ever also becomes admin
    # outside production when this is unset).
    BOOTSTRAP_ADMIN_EMAIL: str | None = None

    # --- Optional AI reranking (never a hard dependency — plan §23) ---------
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-2.0-flash"

    # --- Limits / guards ----------------------------------------------------
    MAX_QUERY_LENGTH: int = 500
    MAX_PAGE_SIZE: int = 100
    DEFAULT_PAGE_SIZE: int = 25

    # --- Internal ------------------------------------------------------------
    # Used for signing local-only artifacts (dev tokens introspection, future
    # share links). Auto-generated per environment when unset — never commit.
    APP_SECRET: str = Field(default_factory=lambda: secrets.token_urlsafe(32))

    @field_validator("APP_ENV", mode="before")
    @classmethod
    def _normalise_env(cls, v: str) -> str:
        return str(v).strip().lower() or "development"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @model_validator(mode="after")
    def _fail_fast_in_production(self) -> Settings:
        """Production safety gate (plan §70 — rules 7 & 8)."""
        if self.is_production:
            problems: list[str] = []
            if self.AUTH_MODE != "firebase":
                problems.append("AUTH_MODE must be 'firebase' in production")
            if not self.FIREBASE_PROJECT_ID:
                problems.append("FIREBASE_PROJECT_ID is required in production")
            if self.DATABASE_URL.startswith("sqlite"):
                problems.append("SQLite is not permitted in production; set DATABASE_URL to PostgreSQL")
            if problems:
                raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached accessor — import this everywhere instead of touching os.environ."""
    return Settings()
