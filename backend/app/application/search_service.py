"""Multi-stage semantic search.

Pipeline:
  query -> text embedding -> ChromaDB top-K -> min-similarity filter
  -> **per-video** temporal grouping (events) -> optional fine-grained local
  search (bounded, cache-aware, idempotent) -> optional LLM rerank
  -> score normalization -> sort -> final results with context frames.

Search modes:
  * ``fast``      — vector retrieval + temporal grouping only.
  * ``accurate``  — + fine search + (optional) rerank.
  * ``metadata``  — filename/metadata-driven match against the library.
"""
from __future__ import annotations

import shutil
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from ..config import Settings
from ..domain.models import Candidate, Frame, FrameType
from ..exceptions import ValidationError
from ..infrastructure.embedding import EmbeddingService
from ..infrastructure.ffmpeg import FFmpegService
from ..infrastructure import metrics
from ..infrastructure.repositories import (
    FeedbackRepository,
    FineCacheRepository,
    FrameRepository,
    SearchHistoryRepository,
    VideoRepository,
)
from ..infrastructure.reranker import LLMReranker
from .context_service import format_context_text
from ..infrastructure.storage import StorageService
from ..infrastructure.vectorstore import VectorStore
from ..logging_config import get_logger
from ..utils import format_hms, format_hms_full, validate_date_bound, validate_id
from ..versioning import FINE_EXTRACTION_VERSION

log = get_logger(__name__)

FINE_FRAME_PREFIX_SEP = "_fine_"
_EMBED_CACHE_MAX = 5000


class SearchService:
    def __init__(
        self,
        settings: Settings,
        videos: VideoRepository,
        frames: FrameRepository,
        history: SearchHistoryRepository,
        feedback: FeedbackRepository,
        storage: StorageService,
        ffmpeg: FFmpegService,
        embedding: EmbeddingService,
        vectorstore: VectorStore,
        reranker: LLMReranker,
        fine_cache: FineCacheRepository | None = None,
        gate=None,
        fine_cache_locks=None,
    ):
        self.settings = settings
        self.videos = videos
        self.frames = frames
        self.history = history
        self.feedback = feedback
        self.storage = storage
        self.ffmpeg = ffmpeg
        self.embedding = embedding
        self.vectorstore = vectorstore
        self.reranker = reranker
        self.fine_cache = fine_cache
        self.gate = gate
        self.fine_cache_locks = fine_cache_locks
        # bound concurrent fine-search extractions across requests
        self._fine_semaphore = threading.BoundedSemaphore(max(1, settings.fine_search_concurrency))
        # count of in-flight fine-search extractions (for maintenance quiescence)
        self._fine_active = 0
        self._fine_active_lock = threading.Lock()
        # in-process fine-frame embedding cache (model-aware key)
        self._emb_cache: OrderedDict[str, object] = OrderedDict()
        self._emb_cache_lock = threading.Lock()
        self._emb_cache_key = (
            f"{self.embedding.metadata().get('embedding_model')}:"
            f"{self.embedding.metadata().get('model_version')}:"
            f"{self.embedding.metadata().get('preprocessing_version')}"
        )
        # in-process query-embedding cache (keyed by normalized query text;
        # the embedding model is fixed per process, so text key is sufficient)
        self._qemb_cache: OrderedDict[str, object] = OrderedDict()
        self._qemb_lock = threading.Lock()
        self._qemb_max = max(1, settings.query_embedding_cache_size)

    @property
    def fine_active(self) -> int:
        with self._fine_active_lock:
            return self._fine_active

    # ------------------------------------------------------------------
    def search(self, query: str, filters: dict) -> dict:
        started = time.time()
        query = (query or "").strip()
        if not query:
            raise ValidationError("query must not be empty")
        if len(query) > self.settings.max_query_length:
            raise ValidationError(
                f"query exceeds MAX_QUERY_LENGTH ({self.settings.max_query_length})"
            )

        mode = (filters.get("mode") or "accurate").strip().lower()
        if mode not in ("fast", "accurate", "metadata"):
            raise ValidationError("mode must be one of: fast, accurate, metadata")

        if mode == "metadata":
            return self._metadata_search(query, filters, started)

        top_k = int(filters.get("top_k") or self.settings.top_k)
        final_results = int(filters.get("final_results") or self.settings.final_results)
        min_similarity = filters.get("min_similarity")
        window = float(
            filters.get("temporal_group_window_seconds")
            or self.settings.temporal_group_window_seconds
        )
        max_per_event = int(
            filters.get("max_results_per_event") or self.settings.max_results_per_event
        )
        fine_search = mode == "accurate" and bool(filters.get("fine_search", True))
        grouping = bool(filters.get("temporal_grouping", True))

        query = self._normalize_query(query)

        where = self._build_where(filters)
        if where is _NO_MATCHES:
            return self._respond(query, mode, started, [], 0)

        # Stage 1: candidate retrieval — optionally via deterministic query
        # expansion + fusion. Single-component queries behave identically to
        # before (regression-safe); connector queries ("X near Y") also match
        # their sub-components.
        with metrics.timed("search.embed"):
            component_queries = self._expand_query(query)
            q_embs = [self._embed_query(q) for q in component_queries]

        with metrics.timed("search.vector_query"):
            candidates = self._fused_candidates(q_embs, top_k=top_k, where=where)
        if min_similarity is not None:
            threshold = float(min_similarity)
            candidates = [c for c in candidates if c.score >= threshold]

        if not candidates:
            return self._respond(query, mode, started, [], 0)

        # per-video temporal grouping -> events
        if grouping:
            events = temporal_group(candidates, window, max_per_event=max_per_event)
        else:
            events = [[c] for c in candidates]

        # fine-grained search on the top events (bounded + cache-aware)
        if fine_search:
            with metrics.timed("search.fine"):
                events = self._fine_search(query, q_embs[0], events, filters)

        # optional LLM rerank (best-effort)
        rerank_status = "skipped"
        if self.reranker.enabled and len(events) > 1:
            with metrics.timed("search.rerank"):
                labelled = [
                    {
                        "key": i,
                        "label": (
                            f"{self._video_name(e[0].video_id)} at "
                            f"{format_hms(e[0].timestamp_seconds)}"
                        )[:200],
                    }
                    for i, e in enumerate(events)
                ]
                ordered = self.reranker.rerank(query, labelled)
                if ordered and len(ordered) == len(events):
                    order = [x["key"] for x in ordered]
                    events = [events[i] for i in order]
                    rerank_status = "applied"
                else:
                    rerank_status = "unavailable"

        # normalize scores + deterministic rerank + sort
        normalize = self.settings.ranking_normalization.strip().lower() == "per_video"
        if normalize:
            self._normalize_scores(events)
        reranked = False
        if self.settings.rerank_deterministic:
            events = self._rerank_events(events)
            events.sort(key=lambda e: e[0].final_score, reverse=True)
            reranked = True
        else:
            events = self._sort_events(events, filters)

        results = []
        videos_cache: dict[str, object] = {}
        model_meta = self.embedding.metadata()
        for i, event in enumerate(events[:final_results]):
            rep = event[0]
            video = videos_cache.get(rep.video_id)
            if video is None:
                video = self.videos.get(rep.video_id)
                videos_cache[rep.video_id] = video
            if video is None:
                continue
            context_frames, ctx_start, ctx_end, ctx_reason, ctx_text, ctx_summary = self._build_context(
                rep, video, query
            )
            # full traceability: every result is traceable to its video, frame,
            # retrieval stage, score, grouping and model/version.
            trace = {
                "video_id": rep.video_id,
                "frame_id": rep.frame_id,
                "retrieval_stage": rep.metadata.get("retrieval_stage", "coarse"),
                "group_event_index": i,
                "group_size": len(event),
                "score": round(rep.score, 6),
                "raw_cosine": round(rep.raw_score, 6),
                "media_type": video.media_type,
                "embedding_model": model_meta.get("embedding_model", ""),
                "model_version": model_meta.get("model_version", ""),
                "indexing_version": model_meta.get("indexing_version", ""),
            }
            results.append({
                "video_id": rep.video_id,
                "video_name": video.original_filename,
                "media_type": video.media_type,
                "timestamp": round(rep.timestamp_seconds, 2),
                "timestamp_hms": format_hms(rep.timestamp_seconds),
                "similarity": round(rep.score, 4),
                "raw_similarity": round(rep.raw_score, 4),
                "frame_id": rep.frame_id,
                "retrieval_stage": trace["retrieval_stage"],
                "frame_url": f"/api/media/{rep.video_id}/frames/{rep.frame_id}",
                "stream_url": f"/api/media/{rep.video_id}/stream",
                "duration": video.duration_seconds,
                "duration_hms": format_hms(video.duration_seconds) if video.duration_seconds else None,
                "width": video.width,
                "height": video.height,
                "uploaded_at": video.uploaded_at,
                "context_frames": context_frames,
                "context_start": round(ctx_start, 2) if ctx_start is not None else None,
                "context_end": round(ctx_end, 2) if ctx_end is not None else None,
                "context_start_hms": format_hms(ctx_start) if ctx_start is not None else None,
                "context_end_hms": format_hms(ctx_end) if ctx_end is not None else None,
                "context_reason": ctx_reason,
                "context_text": ctx_text,
                "context_summary": ctx_summary,
                "final_score": round(rep.final_score, 4) if reranked else round(rep.score, 4),
                "trace": trace,
            })

        took_ms = int((time.time() - started) * 1000)
        try:
            self.history.add(query, filters, len(results), mode=mode, latency_ms=took_ms)
        except Exception:  # pragma: no cover - history must never break search
            pass
        return {
            "query": query,
            "mode": mode,
            "took_ms": took_ms,
            "total_candidates": len(candidates),
            "grouped_events": len(events),
            "semantic_search": self.embedding.semantic,
            "rerank": rerank_status,
            "results": results,
        }

    # ------------------------------------------------------------------
    # query understanding (deterministic — no LLM, bounded cost)
    # ------------------------------------------------------------------
    def _normalize_query(self, query: str) -> str:
        """Cheap, safe normalization: strip quotes, collapse whitespace."""
        import re

        query = query.strip().strip('"').strip("'").strip()
        query = re.sub(r"\s+", " ", query)
        return query

    def _expand_query(self, query: str) -> list[str]:
        """Split a connector query into component queries (plus the full query).

        Only active when ``query_expansion`` is enabled. Components shorter
        than ``query_expansion_min_words`` are dropped, and the total is
        bounded by ``query_expansion_max_components``.
        """
        if not self.settings.query_expansion:
            return [query]
        connectors = [
            c.strip().lower()
            for c in (self.settings.query_expansion_connectors or "").split(",")
            if c.strip()
        ]
        # multi-word connectors first, so "next to" splits before "to"
        connectors.sort(key=len, reverse=True)

        low = query.lower()
        cut_points: set[int] = set()
        for conn in connectors:
            pattern = f" {conn} "
            idx = 0
            while True:
                j = low.find(pattern, idx)
                if j == -1:
                    break
                cut_points.add(j)                 # before the connector
                cut_points.add(j + len(pattern))  # after the connector+spaces
                idx = j + len(pattern)
        if not cut_points:
            return [query]

        # split into components around connector boundaries
        components: list[str] = [query]
        cuts = sorted({0, len(query)} | cut_points)
        for a, b in zip(cuts[:-1], cuts[1:]):
            part = query[a:b].strip(" ,.-")
            words = [w for w in part.split() if w]
            if len(words) >= self.settings.query_expansion_min_words:
                components.append(part)
        # dedupe preserving order, bound the count
        seen: set[str] = set()
        out: list[str] = []
        for c in components:
            key = c.lower()
            if key not in seen:
                seen.add(key)
                out.append(c)
            if len(out) >= self.settings.query_expansion_max_components:
                break
        return out

    def _embed_query(self, query: str) -> object:
        with self._qemb_lock:
            cached = self._qemb_cache.get(query)
            if cached is not None:
                self._qemb_cache.move_to_end(query)
                metrics.inc("search.query_embed_cache.hit")
                return cached
        emb = self.embedding.embed_text([query])[0]
        with self._qemb_lock:
            self._qemb_cache[query] = emb
            self._qemb_cache.move_to_end(query)
            while len(self._qemb_cache) > self._qemb_max:
                self._qemb_cache.popitem(last=False)
        metrics.inc("search.query_embed_cache.miss")
        return emb

    def _fused_candidates(self, q_embs: list, top_k: int, where) -> list[Candidate]:
        """Stage-1 fusion across component-query embeddings.

        * single component -> identical to the pre-expansion behaviour.
        * ``fusion_method=max`` -> a frame's score is its best component score
          (recall-oriented; matches any sub-component).
        * ``fusion_method=sum`` -> scores are summed (precision-oriented).
        """
        if len(q_embs) == 1:
            candidates = self.vectorstore.query(q_embs[0], top_k=top_k, where=where)
            for c in candidates:
                c.full_query_match = True
            return candidates

        import numpy as np

        method = (self.settings.fusion_method or "max").lower()
        per = max(10, int(top_k))
        boost = float(self.settings.fusion_full_query_boost or 0.0)
        by_frame: dict[str, Candidate] = {}
        full_query_frames: set[str] = set()
        for idx, emb in enumerate(q_embs):
            for cand in self.vectorstore.query(emb, top_k=per, where=where):
                if idx == 0:
                    full_query_frames.add(cand.frame_id)
                    cand.full_query_match = True
                existing = by_frame.get(cand.frame_id)
                if existing is None:
                    by_frame[cand.frame_id] = cand
                elif method == "sum":
                    existing.score = float(np.clip(existing.score + cand.score, -1.0, 1.0))
                    if cand.score > existing.raw_score:
                        existing.raw_score = cand.raw_score
                else:  # max
                    if cand.score > existing.score:
                        by_frame[cand.frame_id] = cand
        # keep the FULL query dominant: candidates matched by the full-query
        # embedding receive a small additive boost (recall from components,
        # precision from the full query).
        if boost:
            for frame_id in full_query_frames:
                cand = by_frame.get(frame_id)
                if cand is not None:
                    cand.score = float(np.clip(cand.score + boost, -1.0, 1.0))
        merged = list(by_frame.values())
        merged.sort(key=lambda c: c.score, reverse=True)
        metrics.inc("search.query_expansion.components", len(q_embs) - 1)
        return merged[:top_k]

    # ------------------------------------------------------------------
    def _metadata_search(self, query: str, filters: dict, started: float) -> dict:
        """Metadata-driven search: match the query against filenames/codecs.

        Applies the SAME filters as semantic search (date range, video ids,
        status, media types, duration) so both modes share filter semantics.
        """
        date_from = validate_date_bound(filters.get("date_from"))
        date_to = validate_date_bound(filters.get("date_to"))
        items = self.videos.list(
            search=query,
            status=filters.get("status"),
            date_from=date_from,
            date_to=date_to,
            media_types=filters.get("media_types"),
            min_duration=filters.get("min_duration"),
            max_duration=filters.get("max_duration"),
            media_type=filters.get("media_type"),
            sort_by=filters.get("sort_by") or "uploaded_at",
            sort_order=filters.get("sort_order") or "desc",
            limit=int(filters.get("final_results") or self.settings.final_results),
        )
        # explicit video selection applied after the fact (small list)
        explicit = set(filters.get("video_ids") or [])
        if explicit:
            items = [v for v in items if v.video_id in explicit]
        results = []
        for v in items:
            results.append({
                "video_id": v.video_id,
                "video_name": v.original_filename,
                "media_type": v.media_type,
                "timestamp": 0.0,
                "timestamp_hms": "00:00",
                "similarity": 1.0,
                "raw_similarity": 1.0,
                "frame_id": "",
                "retrieval_stage": "metadata",
                "frame_url": (
                    f"/api/media/{v.video_id}/frames/{v.video_id}_000000"
                    if v.media_type == "image" else ""
                ),
                "stream_url": f"/api/media/{v.video_id}/stream",
                "duration": v.duration_seconds,
                "duration_hms": (None if v.media_type == "image" else (format_hms(v.duration_seconds) if v.duration_seconds else None)),
                "width": v.width,
                "height": v.height,
                "uploaded_at": v.uploaded_at,
                "context_frames": [],
            })
        took_ms = int((time.time() - started) * 1000)
        try:
            self.history.add(query, filters, len(results), mode="metadata", latency_ms=took_ms)
        except Exception:
            pass
        return {
            "query": query,
            "mode": "metadata",
            "took_ms": took_ms,
            "total_candidates": len(results),
            "grouped_events": len(results),
            "semantic_search": self.embedding.semantic,
            "rerank": "skipped",
            "results": results,
        }

    # ------------------------------------------------------------------
    def _build_where(self, filters: dict):
        date_from = validate_date_bound(filters.get("date_from"))
        date_to = validate_date_bound(filters.get("date_to"))
        explicit_videos = filters.get("video_ids") or []

        video_ids: Optional[list[str]] = None
        if date_from or date_to:
            ids = set(self.videos.ids_uploaded_between(date_from or "0000-01-01", date_to or "9999-12-31"))
            video_ids = list(ids)
        if explicit_videos:
            video_ids = explicit_videos if video_ids is None else [v for v in explicit_videos if v in set(video_ids)]

        # additional filters: media type / duration / indexed status
        if filters.get("media_types") or filters.get("min_duration") or filters.get("max_duration"):
            matching = set(self.videos.ids_matching(
                media_types=filters.get("media_types"),
                min_duration=filters.get("min_duration"),
                max_duration=filters.get("max_duration"),
            ))
            video_ids = list(matching) if video_ids is None else [v for v in video_ids if v in matching]
        if filters.get("status"):
            by_status = set(self.videos.ids_matching_status(filters["status"]))
            video_ids = list(by_status) if video_ids is None else [v for v in video_ids if v in by_status]
        if filters.get("media_type"):
            by_kind = set(self.videos.ids_matching_media_type(filters["media_type"]))
            video_ids = list(by_kind) if video_ids is None else [v for v in video_ids if v in by_kind]

        if video_ids is not None and not video_ids:
            return _NO_MATCHES
        if video_ids is None:
            return None
        return {"video_id": {"$in": video_ids}}

    # ------------------------------------------------------------------
    def _fine_search(self, query: str, q_emb, events, filters) -> list:
        max_videos = self.settings.fine_search_max_videos
        deadline = time.time() + self.settings.fine_search_max_duration_seconds
        window = self.settings.fine_search_window_seconds
        interval = self.settings.fine_frame_interval_seconds

        # GLOBAL frame budget shared across ALL events/windows
        remaining_frames = self.settings.fine_search_max_frames
        # distinct-video budget: already-selected videos keep producing
        # candidates; NEW videos are only admitted while the budget allows.
        selected_videos: set[str] = set()
        refined = 0

        for event in events[: self.settings.fine_search_max_timestamps]:
            rep = event[0]

            # -- video budget (correct semantics) --------------------------
            if rep.video_id not in selected_videos:
                if len(selected_videos) >= max_videos:
                    continue  # no more NEW videos allowed
                selected_videos.add(rep.video_id)

            if time.time() > deadline:
                log.info("FINE SEARCH budget exhausted (time)")
                break
            if remaining_frames <= 0:
                log.info("FINE SEARCH global frame budget exhausted")
                break
            if refined >= self.settings.fine_search_max_events:
                break

            video = self.videos.get(rep.video_id)
            if video is None or not video.duration_seconds:
                continue
            lo = max(0.0, rep.timestamp_seconds - window / 2)
            hi = min(video.duration_seconds, rep.timestamp_seconds + window / 2)
            if hi - lo < interval:
                continue
            # consume from the GLOBAL budget for this window
            n_frames = min(int((hi - lo) / interval) + 1, remaining_frames)
            hi = lo + interval * (n_frames - 1)

            best = self._fine_search_window(video, q_emb, lo, hi, interval, n_frames)
            if best is None:
                continue
            # Fine search may only REPLACE the representative when it scores
            # higher (both scores are raw cosines at this stage). This
            # establishes the invariant: fine search narrows or preserves
            # temporal precision — it never degrades a good coarse result with
            # a noisy fine candidate.
            if best.score > rep.score:
                event[0] = best
            refined += 1
            remaining_frames -= n_frames

        if refined:
            log.info(
                "FINE SEARCH refined=%d events (global frames consumed=%d)",
                refined, self.settings.fine_search_max_frames - remaining_frames,
            )
        return events

    def _fine_search_window(self, video, q_emb, lo, hi, interval, expected) -> Optional[Candidate]:
        """Fine-search a single bounded window. Cache-aware, concurrency-safe,
        idempotent, and interval-based.

        * Cache hits require FULL interval coverage of [lo, hi] with a matching
          extraction version. Partial/disjoint caches are detected and only the
          missing gaps are extracted.
        * A complete manifest interval is only committed AFTER every frame of
          that window is persisted + validated (atomic commit) — a crash leaves
          no complete interval, so a partial cache can never masquerade as
          complete.
        * Per-(video, interval) locking with a cache re-check inside the lock
          prevents two concurrent searches from extracting the same window.
        * Global maintenance blocks new fine-cache writes.
        """
        if self.gate is not None and self.gate.active:
            return None  # maintenance: never mutate the cache

        prefix = fine_frame_prefix(video.video_id, interval)
        interval_ms = int(round(interval * 1000))

        # helper: rank frames already on disk for a given window
        def rank_window(win_lo: float, win_hi: float) -> Optional[Candidate]:
            cached = self.frames.fine_between(video.video_id, prefix, win_lo, win_hi)
            return self._rank_cached(cached, q_emb, video) if cached else None

        if self.fine_cache is not None and self.fine_cache_locks is not None:
            key = (video.video_id, interval_ms)
            with self.fine_cache_locks.hold(key):
                # RE-CHECK coverage under the lock
                fully_covered, gaps = self.fine_cache.coverage(
                    video.video_id, interval_ms, FINE_EXTRACTION_VERSION, lo, hi
                )
                if fully_covered:
                    metrics.inc("fine_cache.hit")
                    return rank_window(lo, hi)
                metrics.inc("fine_cache.miss")
                if len(gaps) < 1:
                    return None
                if not (len(gaps) == 1 and abs(gaps[0][0] - lo) < 1e-6 and abs(gaps[0][1] - hi) < 1e-6):
                    metrics.inc("fine_cache.partial")
                # extract ONLY the missing gaps
                candidate: Optional[Candidate] = None
                for (glo, ghi) in gaps:
                    best = self._extract_window(video, q_emb, glo, ghi, interval, prefix)
                    if best is not None and (
                        candidate is None or best.score > candidate.score
                    ):
                        candidate = best
                return candidate

        # no manifest/locks available: extract directly (dev/test fallback)
        return self._extract_window(video, q_emb, lo, hi, interval, prefix)

    def _extract_window(
        self, video, q_emb, lo, hi, interval, prefix
    ) -> Optional[Candidate]:
        """Extract a single window and commit its COMPLETE cache interval."""
        if self.gate is not None and self.gate.active:
            return None
        if not self._fine_semaphore.acquire(timeout=self.settings.fine_search_max_duration_seconds):
            log.warning("fine search semaphore busy; skipping window")
            return None
        with self._fine_active_lock:
            self._fine_active += 1
        try:
            video_path = self.storage.resolve_in(self.settings.media_dir, video.path)
            tmp_dir = self.storage.temp_path(f"fine_{video.video_id}_{int(time.time()*1000)}")
            try:
                samples = self.ffmpeg.extract_frames_range(
                    video_path, tmp_dir, lo, hi, interval,
                    timeout=self.settings.fine_search_max_duration_seconds,
                )
                if not samples:
                    return None
                embs = self.embedding.embed_images([s.path for s in samples])
                sims = embs @ q_emb
                best_i = int(sims.argmax())
                best = samples[best_i]

                frame_dir = self.storage.video_frame_dir(video.video_id)
                interval_ms = int(round(interval * 1000))
                for sample in samples:
                    fid = fine_frame_id(video.video_id, interval, sample.timestamp_seconds)
                    dest = frame_dir / f"{fid}.jpg"
                    if not dest.exists():
                        shutil.copyfile(sample.path, dest)
                    self.frames.upsert(Frame(
                        frame_id=fid,
                        video_id=video.video_id,
                        timestamp_seconds=float(sample.timestamp_seconds),
                        frame_path=self.storage.to_stored_path(dest),
                        frame_type=FrameType.FINE_CACHE.value,
                    ))
                # atomic manifest commit: only after ALL frames persisted
                if self.fine_cache is not None:
                    self.fine_cache.add_interval(
                        video_id=video.video_id,
                        interval_ms=interval_ms,
                        window_start=lo,
                        window_end=hi,
                        frame_count=len(samples),
                        expected_count=max(1, int((hi - lo) / interval) + 1),
                        extraction_version=FINE_EXTRACTION_VERSION,
                    )
                return Candidate(
                    frame_id=fine_frame_id(video.video_id, interval, best.timestamp_seconds),
                    video_id=video.video_id,
                    timestamp_seconds=float(best.timestamp_seconds),
                    score=float(sims[best_i]),
                    raw_score=float(sims[best_i]),
                    frame_path=str(frame_dir / f"{fine_frame_id(video.video_id, interval, best.timestamp_seconds)}.jpg"),
                    video_path=video.path,
                    uploaded_at=None,
                    duration=video.duration_seconds,
                    metadata={"retrieval_stage": "fine"},
                )
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        finally:
            with self._fine_active_lock:
                self._fine_active -= 1
            self._fine_semaphore.release()

    def _embed_cached_images(self, paths: list[Path]):
        """Embed images using the in-process model-aware cache (correctness:
        entries are keyed by model identity, so an incompatible model never
        reuses cached vectors)."""
        import numpy as np

        uncached_idx = []
        for i, p in enumerate(paths):
            key = f"{self._emb_cache_key}:{p}"
            if key not in self._emb_cache:
                uncached_idx.append(i)
        if uncached_idx:
            embs = self.embedding.embed_images([paths[i] for i in uncached_idx])
            with self._emb_cache_lock:
                for i, emb in zip(uncached_idx, embs):
                    key = f"{self._emb_cache_key}:{paths[i]}"
                    self._emb_cache[key] = emb
                    if len(self._emb_cache) > _EMBED_CACHE_MAX:
                        self._emb_cache.popitem(last=False)
        with self._emb_cache_lock:
            out = np.stack([self._emb_cache[f"{self._emb_cache_key}:{p}"] for p in paths])
        return out

    def _rank_cached(self, cached: list[Frame], q_emb, video) -> Optional[Candidate]:
        import numpy as np

        paths = [self.storage.resolve_stored(f.frame_path) for f in cached]
        valid = [(f, p) for f, p in zip(cached, paths) if p.exists()]
        if not valid:
            return None
        embs = self._embed_cached_images([p for _, p in valid])
        sims = embs @ q_emb
        best_i = int(np.asarray(sims).argmax())
        f = valid[best_i][0]
        return Candidate(
            frame_id=f.frame_id,
            video_id=video.video_id,
            timestamp_seconds=f.timestamp_seconds,
            score=float(sims[best_i]),
            raw_score=float(sims[best_i]),
            frame_path=f.frame_path,
            video_path=video.path,
            uploaded_at=None,
            duration=video.duration_seconds,
            metadata={"retrieval_stage": "fine"},
        )

    def _context_frames(self, rep: Candidate) -> list[dict]:
        frames = self.frames.around(rep.video_id, rep.timestamp_seconds, self.settings.context_frames)
        return [
            {
                "frame_id": f.frame_id,
                "timestamp": round(f.timestamp_seconds, 2),
                "timestamp_hms": format_hms(f.timestamp_seconds),
                "frame_url": f"/api/media/{rep.video_id}/frames/{f.frame_id}",
            }
            for f in frames
        ]

    def _build_context(self, rep: Candidate, video, query: str):
        """Compute the temporal context for a result.

        * Video: fetch candidate frames within the window, remove
          near-duplicates (perceptual hash), always keep the matched frame,
          select representative frames spread across the interval, and derive
          the ACTUAL context interval from the selected evidence.
        * Image: the image is its own context.
        * Optional grounded AI summary (GEMINI + llm_context_summary); falls
          back to the deterministic evidence block and never fails context
          generation.
        """
        half = self.settings.context_window_seconds / 2.0

        if video.media_type == "image":
            start = end = 0.0
            context_frames = self._context_frames(rep)
            reason = (
                f'Image matches "{query}" (cosine similarity '
                f"{rep.raw_score:.3f})."
            )
        else:
            ts = rep.timestamp_seconds
            win_start = max(0.0, ts - half)
            win_end = min(video.duration_seconds, ts + half) if video.duration_seconds else ts + half
            # fetch more candidates than we return so dedup/selection have room.
            # Use COARSE frames (the canonical indexed scene frames); fine
            # frames are 0.25s search artifacts that would flood the context.
            limit = max(3 * self.settings.context_max_frames, 10)
            window_frames = self.frames.between(
                rep.video_id, win_start, win_end, limit=limit, frame_type="coarse",
            )
            if not window_frames:
                window_frames = self.frames.between(
                    rep.video_id, win_start, win_end, limit=limit,
                )
            selected = self._select_context_frames(window_frames, rep)
            context_frames = [
                {
                    "frame_id": f.frame_id,
                    "timestamp": round(f.timestamp_seconds, 2),
                    "timestamp_hms": format_hms(f.timestamp_seconds),
                    "frame_url": f"/api/media/{rep.video_id}/frames/{f.frame_id}",
                }
                for f in selected
            ]
            # actual interval from the selected evidence (still window-bounded)
            if selected:
                start = min(f.timestamp_seconds for f in selected)
                end = max(f.timestamp_seconds for f in selected)
                if start == end:  # single frame: fall back to the window
                    start, end = win_start, win_end
            else:
                start, end = win_start, win_end
            n = len(context_frames)
            reason = (
                f'Frame at {format_hms_full(ts)} matches "{query}" '
                f"(cosine similarity {rep.raw_score:.3f})"
                + (f"; {n} representative frame{'s' if n != 1 else ''} within "
                   f"{format_hms_full(start)}–{format_hms_full(end)}." if n else ".")
            )

        result_view = {
            "video_name": video.original_filename,
            "timestamp": rep.timestamp_seconds,
            "context_start": start,
            "context_end": end,
            "similarity": rep.score,
            "context_reason": reason,
            "frames": context_frames,
        }

        # optional grounded summary — never fails context generation
        summary = None
        if self.settings.llm_context_summary and self.reranker.enabled:
            summary = self.reranker.summarize_context(query, format_context_text(result_view, query))

        text = format_context_text(result_view, query, summary=summary)
        return context_frames, start, end, reason, text, summary

    def _select_context_frames(self, frames: list[Frame], rep: Candidate) -> list[Frame]:
        """Pick representative context frames: dedup + keep the matched frame +
        spread across the interval, chronological order, bounded count."""
        if not frames:
            return []
        matched = min(frames, key=lambda f: abs(f.timestamp_seconds - rep.timestamp_seconds))
        rest = [f for f in frames if f.frame_id != matched.frame_id]

        if self.settings.context_dedup and rest:
            from ..infrastructure.perceptual import hamming, phash

            thr = float(self.settings.context_dedup_threshold)
            kept: list[Frame] = []
            prev_hash = self._phash_of(matched)
            for f in sorted(rest, key=lambda x: x.timestamp_seconds):
                h = self._phash_of(f)
                if prev_hash is not None and h is not None and hamming(prev_hash, h) / 64.0 <= thr:
                    continue  # near-duplicate of the previous kept frame
                kept.append(f)
                prev_hash = h
            rest = kept

        max_frames = max(1, self.settings.context_max_frames)
        if len(rest) + 1 <= max_frames:
            selected = [matched] + rest
        else:
            chosen = [matched]
            pool = list(rest)
            for _ in range(max_frames - 1):
                if not pool:
                    break
                best = max(
                    pool,
                    key=lambda f: min(abs(f.timestamp_seconds - c.timestamp_seconds) for c in chosen),
                )
                chosen.append(best)
                pool.remove(best)
            selected = chosen
        selected.sort(key=lambda f: f.timestamp_seconds)
        return selected

    def _phash_of(self, frame: Frame):
        from pathlib import Path

        from ..infrastructure.perceptual import phash

        try:
            path = self.storage.resolve_in_data(frame.frame_path)
            return phash(path)
        except Exception:
            return None

    def _rerank_events(self, events: list[list[Candidate]]) -> list[list[Candidate]]:
        """Deterministic, configurable weighted reranking (no LLM).

        final = w_semantic * semantic
              + w_full_query * full_query_relevance
              + w_neighbor * neighbor
              + w_diversity * diversity
              - w_duplicate * duplicate_penalty

        Documented signals:

        * semantic       — the representative's normalized score (0..1, or raw
          cosine for single-candidate videos/images).
        * full_query     — 1.0 if the representative matched the FULL query
          embedding (keeps the original query dominant over expansion
          components), else 0.
        * neighbor       — mean normalized score of the event's OTHER members
          (temporal coherence: do adjacent frames also match?).
        * diversity      — 1.0 for the best event of each video, else 0
          (video spread in mixed-video results).
        * duplicate      — 1.0 when an event's representative is within 1 s of
          an already-kept higher-ranked event of the SAME video. Temporal
          grouping already merges such frames, so the default weight is 0.0
          (available as a safety net; off by default).

        All weights come from ``rerank_weight_*`` config; defaults are
        conservative and preserve the existing ordering for single-video,
        single-component results.
        """
        w_sem = float(self.settings.rerank_weight_semantic)
        w_fq = float(self.settings.rerank_weight_full_query)
        w_nei = float(self.settings.rerank_weight_neighbor)
        w_div = float(self.settings.rerank_weight_diversity)
        w_dup = float(self.settings.rerank_weight_duplicate)

        seen_videos: set[str] = set()
        seen_by_video: dict[str, list[float]] = {}
        for event in events:
            rep = event[0]
            members = [c.score for c in event]
            others = members[1:] if len(members) > 1 else []
            neighbor = (sum(others) / len(others)) if others else rep.score
            full_query = 1.0 if getattr(rep, "full_query_match", False) else 0.0
            diversity = 0.0
            if rep.video_id not in seen_videos:
                diversity = 1.0
                seen_videos.add(rep.video_id)
            duplicate = 0.0
            for prior_ts in seen_by_video.get(rep.video_id, []):
                if abs(prior_ts - rep.timestamp_seconds) < 1.0:
                    duplicate = 1.0
                    break
            seen_by_video.setdefault(rep.video_id, []).append(rep.timestamp_seconds)
            rep.final_score = float(
                w_sem * rep.score
                + w_fq * full_query
                + w_nei * neighbor
                + w_div * diversity
                - w_dup * duplicate
            )
        return events

    @staticmethod
    def _normalize_scores(events: list[list[Candidate]]) -> None:
        """Per-video z-score + sigmoid so scores from different videos are
        comparable and stay in (0, 1)."""
        import numpy as np

        by_video: dict[str, list[Candidate]] = {}
        for event in events:
            for c in event:
                by_video.setdefault(c.video_id, []).append(c)
        for cands in by_video.values():
            raw = np.array([c.score for c in cands], dtype=np.float64)
            mean, std = raw.mean(), raw.std()
            if std < 1e-9 or len(cands) < 2:
                # a single candidate (e.g. an image is its own "video") has no
                # distribution to normalize against — keep its raw cosine as
                # the score instead of flattening to a neutral 0.5, so image
                # relevance stays meaningful.
                for c in cands:
                    c.raw_score = float(c.score)
                continue
            z = (raw - mean) / std
            normalized = 1.0 / (1.0 + np.exp(-z))
            for c, s in zip(cands, normalized):
                c.raw_score = float(c.score)
                c.score = float(s)

    def _sort_events(self, events, filters) -> list:
        sort_by = filters.get("sort_by") or "relevance"
        if sort_by == "timestamp":
            return sorted(events, key=lambda e: e[0].timestamp_seconds)
        if sort_by == "upload_date":
            rev = (filters.get("sort_order") or "desc") != "asc"
            return sorted(events, key=lambda e: e[0].uploaded_at or 0.0, reverse=rev)
        return sorted(events, key=lambda e: e[0].score, reverse=True)

    def _video_name(self, video_id: str) -> str:
        v = self.videos.get(video_id)
        return v.original_filename if v else video_id

    def _respond(self, query, mode, started, candidates, grouped) -> dict:
        return {
            "query": query,
            "mode": mode,
            "took_ms": int((time.time() - started) * 1000),
            "total_candidates": len(candidates),
            "grouped_events": grouped,
            "semantic_search": self.embedding.semantic,
            "rerank": "skipped",
            "results": [],
        }

    def history_list(self, limit: int = 50) -> list[dict]:
        return self.history.list(limit=limit)

    def history_clear(self) -> dict:
        n = self.history.clear()
        return {"deleted": n}

    def record_feedback(
        self,
        query: str,
        relevant: bool,
        video_id: str | None = None,
        frame_id: str | None = None,
        timestamp: float | None = None,
    ) -> dict:
        if video_id:
            validate_id(video_id, "video_id")
        if frame_id:
            validate_id(frame_id, "frame_id")
        self.feedback.add(query, relevant, video_id, frame_id, timestamp)
        return {"recorded": True}

    def feedback_summary(self, limit: int = 100) -> dict:
        return self.feedback.summary(limit=limit)


class _NoMatches:
    pass


_NO_MATCHES = _NoMatches()


def fine_frame_prefix(video_id: str, interval: float) -> str:
    return f"{video_id}{FINE_FRAME_PREFIX_SEP}{int(round(interval * 1000))}_"


def fine_frame_id(video_id: str, interval: float, timestamp: float) -> str:
    """Deterministic, cacheable id for a fine-search frame."""
    return f"{video_id}{FINE_FRAME_PREFIX_SEP}{int(round(interval * 1000))}_{int(round(timestamp * 1000))}"


def temporal_group(
    candidates: list[Candidate], window: float, max_per_event: int = 1
) -> list[list[Candidate]]:
    """Group candidates into temporal events, *independently per video*.

    Timestamps from different videos can never merge into a single event:
    grouping keys on (video_id, timestamp) so a boundary is always inserted
    when the video changes.

    Example across two videos:
      A@10, A@12, B@11  (window=5)  ->  [A@10, A@12], [B@11]
    """
    ordered = sorted(candidates, key=lambda c: (c.video_id, c.timestamp_seconds))
    events: list[list[Candidate]] = []
    current: list[Candidate] = []
    group_start = 0.0
    current_video: Optional[str] = None

    for cand in ordered:
        if not current:
            current = [cand]
            group_start = cand.timestamp_seconds
            current_video = cand.video_id
            continue
        same_video = cand.video_id == current_video
        within_window = (cand.timestamp_seconds - group_start) <= window
        if not (same_video and within_window):
            _close_event(events, current, max_per_event)
            current = [cand]
            group_start = cand.timestamp_seconds
            current_video = cand.video_id
        else:
            current.append(cand)
    if current:
        _close_event(events, current, max_per_event)

    # events ranked by their best (representative) score
    events.sort(key=lambda e: e[0].score, reverse=True)
    return events


def _close_event(events: list, current: list[Candidate], max_per_event: int) -> None:
    current.sort(key=lambda c: c.score, reverse=True)
    events.append(current[: max(1, max_per_event)])
