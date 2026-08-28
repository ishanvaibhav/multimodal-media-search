from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(max_length=1000)
    mode: str = "accurate"               # fast | accurate | metadata
    video_ids: Optional[list[str]] = Field(default=None, max_length=100)
    date_from: Optional[str] = None      # ISO date, e.g. 2026-08-01
    date_to: Optional[str] = None        # ISO date, e.g. 2026-08-17
    min_similarity: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    top_k: Optional[int] = Field(default=None, ge=1, le=500)
    final_results: Optional[int] = Field(default=None, ge=1, le=100)
    fine_search: bool = True
    temporal_grouping: bool = True
    temporal_group_window_seconds: Optional[float] = Field(default=None, gt=0, le=3600)
    max_results_per_event: Optional[int] = Field(default=None, ge=1, le=50)
    sort_by: str = "relevance"           # relevance | timestamp | upload_date
    sort_order: str = "desc"
    # additional filters
    media_types: Optional[list[str]] = Field(default=None, max_length=20)
    min_duration: Optional[float] = Field(default=None, ge=0)
    max_duration: Optional[float] = Field(default=None, ge=0)
    status: Optional[str] = None
    media_type: Optional[str] = None        # video | image (unified search filter)


class ContextFrame(BaseModel):
    frame_id: str
    timestamp: float
    timestamp_hms: str
    frame_url: str


class SearchResult(BaseModel):
    video_id: str
    video_name: str
    media_type: str = "video"
    timestamp: float
    timestamp_hms: str
    similarity: float
    raw_similarity: float = 0.0
    frame_id: str
    retrieval_stage: str = "coarse"
    frame_url: str
    stream_url: str
    duration: Optional[float] = None
    duration_hms: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    uploaded_at: Optional[str] = None
    context_frames: list[ContextFrame] = []
<<<<<<< HEAD
    context_start: Optional[float] = None
    context_end: Optional[float] = None
    context_start_hms: Optional[str] = None
    context_end_hms: Optional[str] = None
    context_reason: Optional[str] = None
    context_text: Optional[str] = None
    context_summary: Optional[str] = None
    final_score: Optional[float] = None
=======
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
    trace: dict = {}


class SearchResponse(BaseModel):
    query: str
    mode: str = "accurate"
    took_ms: int
    total_candidates: int
    grouped_events: int
    semantic_search: bool = True
    rerank: str = "skipped"
    results: list[SearchResult]


class SearchHistoryItem(BaseModel):
    id: int
    query: str
    filters: Optional[str] = None
    result_count: int
    mode: Optional[str] = None
    latency_ms: Optional[int] = None
    created_at: str


class SearchHistoryResponse(BaseModel):
    items: list[SearchHistoryItem]


class FeedbackRequest(BaseModel):
    query: str
    relevant: bool
    video_id: Optional[str] = None
    frame_id: Optional[str] = None
    timestamp: Optional[float] = None


class FeedbackResponse(BaseModel):
    recorded: bool
<<<<<<< HEAD


class ContextSaveRequest(BaseModel):
    query: str
    video_id: str
    filename: Optional[str] = None
    media_type: str = "video"
    timestamp_seconds: Optional[float] = None
    timestamp: Optional[float] = None
    context_start: Optional[float] = None
    context_end: Optional[float] = None
    score: Optional[float] = None
    similarity: Optional[float] = None
    frame_id: Optional[str] = None
    context_text: Optional[str] = None
    context_frames: Optional[list] = None
    reason: Optional[str] = None


class SavedContextOut(BaseModel):
    id: int
    query: str
    video_id: str
    filename: str
    media_type: str = "video"
    timestamp_seconds: float = 0.0
    timestamp_hms: str = "00:00"
    context_start: Optional[float] = None
    context_end: Optional[float] = None
    score: float = 0.0
    frame_id: Optional[str] = None
    context_text: Optional[str] = None
    context_frames: list = []
    reason: Optional[str] = None
    created_at: Optional[str] = None


class SavedContextList(BaseModel):
    items: list[SavedContextOut]


class ContextDeleteResponse(BaseModel):
    deleted: int
=======
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
