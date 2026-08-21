from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class AdminClearRequest(BaseModel):
    confirmation: str = Field(description="must equal 'DELETE ALL' to proceed")


class AdminClearResponse(BaseModel):
    cleared: bool
    deleted: dict


class MaintenanceState(BaseModel):
    maintenance: bool


class SystemInfo(BaseModel):
    app_name: str
    app_env: str
    version: str
    python: str
    embedding_backend: str
    semantic_search: bool = False
    model: str = ""
    embedding_dim: int
    embedding_device: str
    ffmpeg: str
    ffprobe: str
    chroma_collection: str
    data_dir: str
    storage: dict
    resources: dict = {}
    admin_auth: str = "open"
    budgets: dict = {}


class HealthResponse(BaseModel):
    api: str
    database: str
    chromadb: str
    ffmpeg: str
    ffprobe: str
    embedding_model: str
    storage: str
    worker: str
    details: dict = {}
