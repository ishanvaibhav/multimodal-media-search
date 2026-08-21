"""Search endpoints (modes, history, relevance feedback)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, Request

from ..schemas.search import (
    FeedbackRequest,
    FeedbackResponse,
    SearchHistoryResponse,
    SearchRequest,
    SearchResponse,
)
from .deps import get_container

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(body: SearchRequest, request: Request):
    container = get_container(request)
    filters = {
        "mode": body.mode,
        "video_ids": body.video_ids,
        "date_from": body.date_from,
        "date_to": body.date_to,
        "min_similarity": body.min_similarity,
        "top_k": body.top_k,
        "final_results": body.final_results,
        "fine_search": body.fine_search,
        "temporal_grouping": body.temporal_grouping,
        "temporal_group_window_seconds": body.temporal_group_window_seconds,
        "max_results_per_event": body.max_results_per_event,
        "sort_by": body.sort_by,
        "sort_order": body.sort_order,
        "media_types": body.media_types,
        "min_duration": body.min_duration,
        "max_duration": body.max_duration,
        "status": body.status,
        "media_type": body.media_type,
    }
    return await asyncio.to_thread(container.search_service.search, body.query, filters)


@router.post("/feedback", response_model=FeedbackResponse)
async def record_feedback(body: FeedbackRequest, request: Request):
    container = get_container(request)
    return container.search_service.record_feedback(
        body.query, body.relevant, body.video_id, body.frame_id, body.timestamp
    )


@router.get("/feedback")
async def feedback_summary(request: Request, limit: int = Query(100, ge=1, le=1000)):
    return get_container(request).search_service.feedback_summary(limit=limit)


@router.get("/history", response_model=SearchHistoryResponse)
async def search_history(request: Request, limit: int = Query(50, ge=1, le=500)):
    items = get_container(request).search_service.history_list(limit=limit)
    return SearchHistoryResponse(items=items)


@router.delete("/history")
async def clear_search_history(request: Request):
    return get_container(request).search_service.history_clear()
