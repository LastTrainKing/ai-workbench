"""Domain error taxonomy + FastAPI exception handlers.

Design goals
------------
* Business code raises typed errors (never bare ``Exception``).
* Every error carries a stable machine-readable ``code`` and an HTTP status.
* Unexpected errors are logged with a stack trace and never leaked to clients
  in production mode.
* No silent ``except: pass`` anywhere in the codebase.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_var

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for all expected application errors."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "app_error"

    def __init__(
        self,
        message: str = "Application error",
        *,
        details: Any = None,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


# --------------------------------------------------------------------------
# 4xx — client errors
# --------------------------------------------------------------------------
class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class RunNotFoundError(NotFoundError):
    """A run that does not exist, or does not belong to the caller.

    **Its own class, because the status code alone was ambiguous.** Run-scoped routes used
    to raise `ValidationError`, which is 422 with `code="validation_error"` — the exact
    response FastAPI produces when a request body or path parameter fails validation. Two
    completely different facts produced byte-identical errors: "the run is not yours" and
    "your request was malformed". A client, a test, or a person reading a log could not
    tell them apart, and a test asserting "422 means access was denied" would pass just as
    happily when the request was simply broken.

    404 rather than 403, and the same answer for "missing" and "not yours", so the response
    does not confirm that a run id exists to a caller with no right to know. The distinct
    `code` is what makes it checkable.
    """

    code = "run_not_found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_error"


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"


class UnsupportedMediaTypeError(AppError):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    code = "unsupported_media_type"


class PayloadTooLargeError(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "payload_too_large"


class RateLimitedError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


# --------------------------------------------------------------------------
# 5xx / infrastructure errors
# --------------------------------------------------------------------------
class InfrastructureError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "infrastructure_error"


class VectorStoreError(InfrastructureError):
    code = "vector_store_error"


class CacheError(InfrastructureError):
    code = "cache_error"


class StorageError(InfrastructureError):
    code = "storage_error"


# --------------------------------------------------------------------------
# AI errors — classified so the caller can decide retry vs. fail fast
# --------------------------------------------------------------------------
class AIError(AppError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "ai_error"
    retryable = False


class AIProviderError(AIError):
    """Transport / 5xx / rate-limit problems coming from the model vendor."""

    code = "ai_provider_error"
    retryable = True


class AITimeoutError(AIProviderError):
    code = "ai_timeout"
    retryable = True


class AIRateLimitError(AIProviderError):
    code = "ai_rate_limited"
    retryable = True
    status_code = status.HTTP_429_TOO_MANY_REQUESTS


class AIAuthError(AIError):
    """Bad or missing API key — retrying will not help."""

    code = "ai_auth_error"
    retryable = False


class AIConfigurationError(AIError):
    code = "ai_configuration_error"
    retryable = False


class AIResponseFormatError(AIError):
    """Model returned content we could not parse (e.g. invalid JSON)."""

    code = "ai_response_format_error"
    retryable = True


class DocumentParseError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "document_parse_error"


class ToolExecutionError(AppError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "tool_execution_error"


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------
def _envelope(
    request: Request,
    *,
    code: str,
    message: str,
    details: Any = None,
    status_code: int,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {"code": code, "message": message, "details": details},
            "request_id": request_id_var.get(),
        },
        headers={"X-Request-ID": request_id_var.get()},
    )


def register_exception_handlers(app: FastAPI) -> None:
    from app.core.config import settings

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        log = logger.warning if exc.status_code < 500 else logger.error
        log(
            "app_error",
            extra={
                "code": exc.code,
                "status": exc.status_code,
                "path": request.url.path,
                "detail": exc.message,
            },
        )
        return _envelope(
            request,
            code=exc.code,
            message=exc.message,
            details=exc.details,
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Flatten pydantic errors into something the frontend can render inline.
        fields = []
        for err in exc.errors():
            loc = [str(p) for p in err.get("loc", []) if p not in ("body", "query", "path")]
            fields.append(
                {
                    "field": ".".join(loc) or "<root>",
                    "message": err.get("msg", "invalid value"),
                    "type": err.get("type", "value_error"),
                }
            )
        logger.info(
            "request_validation_failed",
            extra={"path": request.url.path, "fields": [f["field"] for f in fields]},
        )
        return _envelope(
            request,
            code="validation_error",
            message="Request validation failed",
            details={"fields": fields},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: "authentication_error",
            403: "permission_denied",
            404: "not_found",
            405: "method_not_allowed",
        }.get(exc.status_code, "http_error")
        return _envelope(
            request,
            code=code,
            message=str(exc.detail),
            status_code=exc.status_code,
        )

    @app.exception_handler(SQLAlchemyError)
    async def _db(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("database_error", extra={"path": request.url.path})
        return _envelope(
            request,
            code="database_error",
            message="A database error occurred"
            if not settings.debug
            else f"{type(exc).__name__}: {exc}",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_exception",
            extra={"path": request.url.path, "type": type(exc).__name__},
        )
        return _envelope(
            request,
            code="internal_error",
            message=f"{type(exc).__name__}: {exc}" if settings.debug else "Internal server error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
