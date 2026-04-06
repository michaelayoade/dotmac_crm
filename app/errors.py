from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.datastructures import UploadFile

from app.auth_exceptions import AuthenticationRequired
from app.logging import get_logger

logger = get_logger(__name__)

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_MARKERS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
)


def _error_payload(code: str, message: str, details):
    return {"code": code, "message": message, "details": details}


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def _sanitize_validation_value(value, *, key: str | None = None):
    if key and _is_sensitive_key(key):
        return _REDACTED
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, UploadFile):
        return value.filename or "upload"
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize_validation_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [_sanitize_validation_value(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def register_error_handlers(app) -> None:
    @app.exception_handler(AuthenticationRequired)
    async def auth_required_handler(request: Request, exc: AuthenticationRequired):
        """Redirect to login page when authentication is required."""
        response = RedirectResponse(url=exc.redirect_url, status_code=303)
        if request.headers.get("HX-Request") == "true":
            response.headers["HX-Redirect"] = exc.redirect_url
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        detail = exc.detail
        code = f"http_{exc.status_code}"
        message = "Request failed"
        details = None
        if isinstance(detail, dict):
            code = detail.get("code", code)
            message = detail.get("message", message)
            details = detail.get("details")
        elif isinstance(detail, str):
            message = detail
        else:
            details = detail
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(code, message, details),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        # Keep validation details serializable while redacting secrets from payload echoes.
        errors = _sanitize_validation_value(exc.errors())
        return JSONResponse(
            status_code=422,
            content=_error_payload("validation_error", "Validation error", errors),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.error(
            "unhandled_exception path=%s method=%s error=%s",
            request.url.path,
            request.method,
            exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content=_error_payload("internal_error", "Internal server error", None),
        )
