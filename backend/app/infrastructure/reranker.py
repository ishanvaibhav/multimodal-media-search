"""Optional LLM reranking (Gemini) for the final candidate events.

Kept OUT of the hot path: it only ever sees the top few temporal events that
survived coarse retrieval + grouping + fine search. If GEMINI_API_KEY is unset
or LLM_RERANK is disabled, reranking is skipped gracefully and the system keeps
working entirely offline (vector-ranked results are returned).

Safety: candidate labels are truncated and restricted to timestamps/video
names; user-controlled metadata is never injected into the prompt. Output is
validated to be a permutation of the input indexes; any failure returns the
original order unchanged.
"""
from __future__ import annotations

import json
import re
<<<<<<< HEAD
from typing import Optional
=======
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424

import httpx

from ..config import Settings
from ..logging_config import get_logger
from . import metrics

log = get_logger(__name__)

_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
_MAX_CANDIDATES = 20


class LLMReranker:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.gemini_api_key) and self.settings.llm_rerank

<<<<<<< HEAD
    def summarize_context(self, query: str, evidence: str) -> Optional[str]:
        """Generate a concise, GROUNDED summary of retrieved context evidence.

        Returns None (and is never allowed to fail context generation) if the
        LLM is unavailable, times out, or returns malformed output. The prompt
        explicitly forbids inventing content not present in the evidence.
        """
        if not self.enabled:
            return None
        prompt = (
            "You are summarising video-search context. Write ONE concise "
            "sentence describing what the retrieved segment shows, grounded "
            "ONLY in the evidence below. Do not invent objects, people or "
            "actions that are not listed.\n\n"
            f'Query: "{query[:300]}"\n\nEvidence:\n{evidence[:1500]}'
        )
        try:
            resp = httpx.post(
                _GEMINI_ENDPOINT.format(model=self.settings.gemini_model),
                params={"key": self.settings.gemini_api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 64},
                },
                timeout=self.settings.gemini_timeout_seconds,
            )
            if resp.status_code == 429:
                metrics.inc("context_summary.rate_limited")
                return None
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            text = text.strip().strip('"')
            return text if text else None
        except Exception as exc:  # noqa: BLE001
            metrics.inc("context_summary.errors")
            log.warning("context summary failed, using evidence only: %s", exc)
            return None

=======
>>>>>>> 7ed4cd97f55f17cc4833815b4ed7fa39656cb424
    def rerank(self, query: str, events: list[dict]) -> list[dict]:
        """Reorder events by LLM judgment. Returns the same list (reordered).

        Each event dict must include a stable 'key' and a 'label'. On any
        failure the original order is returned unchanged (graceful degradation).
        """
        if not self.enabled or len(events) <= 1:
            return events
        max_candidates = int(getattr(self.settings, "rerank_max_candidates", _MAX_CANDIDATES) or _MAX_CANDIDATES)
        events = events[: max_candidates]

        # sanitise: short, single-line labels only
        options = "\n".join(
            f"{i}: {str(e.get('label', ''))[:200].replace(chr(10), ' ')}"
            for i, e in enumerate(events)
        )
        prompt = (
            'You are ranking video search results for the query: "'
            + query[:500].replace('"', "'")
            + '"\n\nCandidate moments:\n'
            + options
            + "\n\nReply with ONLY a JSON array of integer indexes, best first, "
            "e.g. [2,0,1]. Include every index exactly once."
        )
        try:
            resp = httpx.post(
                _GEMINI_ENDPOINT.format(model=self.settings.gemini_model),
                params={"key": self.settings.gemini_api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": int(getattr(self.settings, "llm_max_tokens", 64) or 64)},
                },
                timeout=self.settings.gemini_timeout_seconds,
            )
            if resp.status_code == 429:
                metrics.inc("rerank.rate_limited")
                log.warning("Gemini rerank rate-limited; keeping original order")
                return events
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            match = re.search(r"\[[0-9,\s]+\]", text)
            order = json.loads(match.group(0)) if match else []
            if set(order) == set(range(len(events))):
                metrics.inc("rerank.applied")
                return [events[i] for i in order]
            log.warning("Gemini rerank returned an invalid permutation; ignoring")
        except Exception as exc:  # noqa: BLE001
            metrics.inc("rerank.errors")
            log.warning("LLM rerank failed, keeping original order: %s", exc)
        return events
