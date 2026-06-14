from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth_exceptions import AuthenticationRequired
from app.logging import get_logger

logger = get_logger(__name__)

# Status codes that have a dedicated styled HTML error page.
_HTML_ERROR_TEMPLATES = {403: "errors/403.html", 404: "errors/404.html"}


def _wants_html(request: Request) -> bool:
    """True for browser navigations (HTML-accepting, non-API, non-HTMX) so we can
    return a styled error page instead of raw JSON (NOTE-052 / BUG-051 / BUG-090)."""
    if request.url.path.startswith(("/api/", "/metrics", "/health")):
        return False
    if request.headers.get("HX-Request") == "true":
        return False
    accept = request.headers.get("accept", "")
    return "text/html" in accept


def _render_html_error(request: Request, status_code: int, message: str) -> HTMLResponse | None:
    """Render a styled error page if one applies; return None to fall back to JSON."""
    from app.web.templates import Jinja2Templates

    template = _HTML_ERROR_TEMPLATES.get(status_code, "errors/500.html" if status_code >= 500 else None)
    if not template:
        return None
    try:
        templates = Jinja2Templates(directory="templates")
        return templates.TemplateResponse(
            template,
            {"request": request, "message": message},
            status_code=status_code,
        )
    except Exception:
        return None


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

    @app.exception_handler(StarletteHTTPException)
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
        if _wants_html(request):
            html = _render_html_error(request, exc.status_code, message)
            if html is not None:
                return html
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
        if _wants_html(request):
            html = _render_html_error(request, 500, "Something went wrong on our end.")
            if html is not None:
                return html
        return JSONResponse(
            status_code=500,
            content=_error_payload("internal_error", "Internal server error", None),
        )
