"""Domain exception hierarchy and FastAPI exception handlers.

Services raise these semantic exceptions instead of ``HTTPException`` so business logic
stays framework-agnostic and unit-testable. The handlers installed by
:func:`register_exception_handlers` translate them into consistent JSON error responses:

    {"error": {"code": "not_found", "message": "..."}}
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for expected, handled application errors.

    :param message: human-readable message safe to return to the client.
    :param code: stable machine-readable error code (snake_case).
    :param status_code: HTTP status to respond with.
    """

    code: str = "app_error"
    status_code: int = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class NotFoundError(AppError):
    code = "not_found"
    status_code = status.HTTP_404_NOT_FOUND


class ValidationError(AppError):
    code = "validation_error"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class ConflictError(AppError):
    code = "conflict"
    status_code = status.HTTP_409_CONFLICT


class AuthError(AppError):
    code = "unauthorized"
    status_code = status.HTTP_401_UNAUTHORIZED


class ForbiddenError(AppError):
    code = "forbidden"
    status_code = status.HTTP_403_FORBIDDEN


def _error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def register_exception_handlers(app: FastAPI) -> None:
    """Install JSON error handlers for domain and validation errors."""

    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        # 5xx would be a bug; AppError is always a client-facing 4xx, log at info.
        logger.info("AppError %s: %s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Surface the first error concisely; full detail stays in exc.errors().
        errors = exc.errors()
        message = "Request validation failed"
        if errors:
            first = errors[0]
            loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
            message = f"{loc}: {first.get('msg')}" if loc else str(first.get("msg"))
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_body("validation_error", message),
        )
