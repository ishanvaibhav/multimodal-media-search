"""Application-level exception hierarchy.

Every exception carries an HTTP status code and a stable machine-readable
``code`` so the API can return consistent error payloads while the backend log
keeps the full technical detail.
"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all application errors."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, detail: Any = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationError(AppError):
    status_code = 400
    code = "validation_error"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class UploadError(ValidationError):
    code = "upload_error"


class MediaProcessingError(AppError):
    status_code = 422
    code = "media_processing_error"


class FFmpegNotFoundError(AppError):
    status_code = 503
    code = "ffmpeg_not_found"


class ModelUnavailableError(AppError):
    status_code = 503
    code = "model_unavailable"


class EmbeddingError(AppError):
    status_code = 500
    code = "embedding_error"


class VectorStoreError(AppError):
    status_code = 500
    code = "vector_store_error"


class DatabaseError(AppError):
    status_code = 500
    code = "database_error"


class StorageError(AppError):
    status_code = 507
    code = "storage_error"


class JobCancelled(AppError):
    status_code = 409
    code = "job_cancelled"
