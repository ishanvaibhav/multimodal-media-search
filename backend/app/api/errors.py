"""Standard API envelope (master plan §45).

Success:  {"success": true,  "data": ...}
Error:    {"success": false, "error": {"code", "message", "request_id"}}

Every endpoint either returns an ``Ok[...]`` schema or raises ``AppError`` /
``HTTPException`` — the handlers here guarantee the outer shape is identical
for clients in all cases.
"""

from __future__ import annotations

import logging
from typing import Generic, Literal, TypeVar

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pydantic.generics import GenericModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..core.logging import get_request_id

log = logging.getLogger(__name__)

T = TypeVar("T")


class Ok(GenericModel, Generic[T]):
    success: Literal[True] = True
    data: T


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None


class ErrorEnvelope(BaseModel):
    success: Literal[False] = False
    error: ErrorBody


class AppError(Exception):
    """Domain error → error envelope. Raise this anywhere in the stack."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    # Common constructors
    @classmethod
    def not_found(cls, what: str = "resource") -> AppError:
        return cls(f"{what.upper()}_NOT_FOUND", f"{what} was not found", status.HTTP_404_NOT_FOUND)

    @classmethod
    def forbidden(cls, message: str = "insufficient permissions") -> AppError:
        return cls("FORBIDDEN", message, status.HTTP_403_FORBIDDEN)

    @classmethod
    def unauthenticated(cls, message: str = "authentication required") -> AppError:
        return cls("UNAUTHENTICATED", message, status.HTTP_401_UNAUTHORIZED)

    @classmethod
    def conflict(cls, code: str, message: str) -> AppError:
        return cls(code, message, status.HTTP_409_CONFLICT)


def _envelope(code: str, message: str, status_code: int) -> JSONResponse:
    body = ErrorEnvelope(error=ErrorBody(code=code, message=message, request_id=get_request_id()))
    return JSONResponse(status_code=status_code, content=jsonable_encoder(body))


def register_exception_handlers(app: FastAPI) -> None:
    from ..auth.service import AuthError

    @app.exception_handler(AppError)
    async def _app_error(_req: Request, exc: AppError) -> JSONResponse:
        return _envelope(exc.code, exc.message, exc.status_code)

    @app.exception_handler(AuthError)
    async def _auth_error(_req: Request, exc: AuthError) -> JSONResponse:
        return _envelope(exc.code, str(exc), exc.status_code)

    # Registered on the Starlette base class so routing-level 404/405s take
    # the envelope too (FastAPI's own HTTPException subclass is covered by
    # the same MRO lookup).
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: "UNAUTHENTICATED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            413: "PAYLOAD_TOO_LARGE",
            422: "VALIDATION_ERROR",
            429: "RATE_LIMITED",
        }.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ").title()
        return _envelope(code, message, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_req: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query", "path"))
        message = f"{loc}: {first.get('msg', 'invalid request')}" if loc else "invalid request"
        return _envelope("VALIDATION_ERROR", message, status.HTTP_422_UNPROCESSABLE_ENTITY)

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error: %s", exc)
        return _envelope("INTERNAL_ERROR", "an internal error occurred", 500)
