"""HTTP middleware: request IDs + structured access logging (plan §46)."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..core.logging import bind_request_id

log = logging.getLogger("aimhub.access")

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns/propagates ``X-Request-ID`` and logs one line per request with
    method, path, status and latency — the trace key an admin uses to follow
    a failure across API → worker → database (plan §46)."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:24]
        request.state.request_id = request_id
        bind_request_id(request_id)

        start = time.perf_counter()
        response = await call_next(request)
        latency_ms = (time.perf_counter() - start) * 1000

        response.headers[REQUEST_ID_HEADER] = request_id
        log.info(
            "%s %s -> %s %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            latency_ms,
        )
        return response
