"""Context service: structured context formatting, saving, and export.

The context representation is deterministic and grounded — it never invents
evidence. The "reason" line cites the matched frame, its cosine similarity,
the query, and the neighbouring-frame segment. (An optional LLM summary can
layer on top later without changing this contract.)
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Optional

from ..domain.models import SavedContext
from ..exceptions import NotFoundError
from ..infrastructure.repositories import SavedContextRepository
from ..logging_config import get_logger
from ..utils import format_hms_full, validate_id

log = get_logger(__name__)


def format_context_text(result: dict, query: str, summary: str | None = None) -> str:
    """Plain-text context block (no markup) with clearly separated sections:

    * RETRIEVED EVIDENCE — deterministic, grounded facts only.
    * AI SUMMARY          — optional grounded LLM summary (never hallucinated).

    The evidence block is always produced; the summary section only appears
    when a summary string is supplied.
    """
    media = result.get("video_name", "")
    ts = result.get("timestamp_seconds") or result.get("timestamp") or 0.0
    start = result.get("context_start")
    end = result.get("context_end")
    relevance = result.get("similarity", 0.0)
    reason = result.get("context_reason", "")

    lines = [
        "----------------------------------------",
        "RETRIEVED EVIDENCE",
        "",
        f"Media: {media}",
        "",
        f'Query: "{query}"',
        f"Match: {format_hms_full(ts)}",
    ]
    if start is not None and end is not None:
        lines.append(f"Segment: {format_hms_full(start)} - {format_hms_full(end)}")
    lines.append(f"Relevance: {relevance:.2f}")
    frames = result.get("frames") or []
    if frames:
        stamps = "\n".join(
            f"  {f.get('timestamp_hms') or format_hms_full(f.get('timestamp', 0))}"
            for f in frames
        )
        lines.append("Representative frames:")
        lines.append(stamps)
    if reason:
        lines.append(f"Reason: {reason}")
    lines.append("----------------------------------------")

    if summary:
        lines += [
            "",
            "----------------------------------------",
            "AI SUMMARY",
            "",
            summary,
            "----------------------------------------",
        ]
    return "\n".join(lines)


class ContextService:
    def __init__(self, repo: SavedContextRepository):
        self.repo = repo

    # ------------------------------------------------------------------
    def save(self, payload: dict) -> SavedContext:
        validate_id(payload.get("video_id", ""), "video_id")
        query = (payload.get("query") or "").strip()
        if not query:
            from ..exceptions import ValidationError

            raise ValidationError("query must not be empty")
        frames = payload.get("context_frames") or []
        ctx = SavedContext(
            query=query,
            video_id=payload.get("video_id", ""),
            filename=payload.get("filename") or payload.get("video_name") or "",
            media_type=payload.get("media_type", "video"),
            timestamp_seconds=float(payload.get("timestamp_seconds") or payload.get("timestamp") or 0.0),
            context_start=payload.get("context_start"),
            context_end=payload.get("context_end"),
            score=float(payload.get("score") or payload.get("similarity") or 0.0),
            frame_id=payload.get("frame_id"),
            context_text=payload.get("context_text"),
            context_frames_json=json.dumps(frames) if frames else None,
            reason=payload.get("reason"),
        )
        return self.repo.insert(ctx)

    def list(self, limit: int = 100) -> list[SavedContext]:
        return self.repo.list(limit=limit)

    def delete(self, ctx_id: int) -> dict:
        if self.repo.delete(ctx_id) == 0:
            raise NotFoundError(f"saved context '{ctx_id}' not found")
        return {"deleted": ctx_id}

    # ------------------------------------------------------------------
    def export(self, fmt: str = "txt", limit: int = 1000) -> dict:
        items = self.repo.list(limit=limit)
        fmt = (fmt or "txt").lower()
        if fmt == "json":
            payload = json.dumps([_export_dict(s) for s in items], indent=2, ensure_ascii=False)
            media_type, ext = "application/json", "json"
        elif fmt == "csv":
            buf = io.StringIO()
            writer = csv.DictWriter(
                buf,
                fieldnames=["query", "filename", "media_type", "timestamp",
                            "context_start", "context_end", "score", "context_text"],
            )
            writer.writeheader()
            for s in items:
                writer.writerow({
                    "query": s.query,
                    "filename": s.filename,
                    "media_type": s.media_type,
                    "timestamp": round(s.timestamp_seconds, 2),
                    "context_start": s.context_start,
                    "context_end": s.context_end,
                    "score": round(s.score, 4),
                    "context_text": (s.context_text or "").replace("\n", " "),
                })
            payload = buf.getvalue()
            media_type, ext = "text/csv", "csv"
        else:
            blocks = []
            for s in items:
                start = format_hms_full(s.context_start) if s.context_start is not None else ""
                end = format_hms_full(s.context_end) if s.context_end is not None else ""
                blocks.append(
                    "\n".join([
                        "----------------------------------------",
                        f"Media: {s.filename}",
                        "",
                        f'Query: "{s.query}"',
                        f"Match: {format_hms_full(s.timestamp_seconds)}",
                        (f"Segment: {start} - {end}" if start and end else ""),
                        f"Relevance: {s.score:.2f}",
                        "----------------------------------------",
                    ]).strip()
                )
            payload = "\n\n".join(blocks)
            media_type, ext = "text/plain", "txt"
        return {"content": payload, "media_type": media_type, "extension": ext, "count": len(items)}


def _export_dict(s: SavedContext) -> dict:
    return {
        "query": s.query,
        "media": s.filename,
        "media_type": s.media_type,
        "timestamp": round(s.timestamp_seconds, 2),
        "start": s.context_start,
        "end": s.context_end,
        "score": round(s.score, 4),
        "frame_id": s.frame_id,
        "context_frames": s.context_frames,
        "reason": s.reason,
        "context": s.context_text,
    }
