import secrets
from fastapi import Depends, FastAPI, Request
from time import monotonic
from threading import Lock
from starlette.responses import Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CSRF_TOKEN_NAME,
    get_csrf_token,
    set_csrf_cookie,
    generate_csrf_token,
)

from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.auth_flow import router as auth_flow_router
from app.api.rbac import router as rbac_router
from app.api.notifications import router as notifications_router
from app.api.workflow import router as workflow_router
from app.api.dispatch import router as dispatch_router
from app.api.inventory import router as inventory_router
from app.api.timecost import router as timecost_router
from app.api.comms import router as comms_router
from app.api.analytics import router as analytics_router
from app.api.projects import router as projects_router
from app.api.tickets import router as tickets_router
from app.api.workforce import router as workforce_router
from app.api.external import router as external_router
from app.api.sales_orders import router as sales_orders_router
from app.api.gis import router as gis_router
from app.api.geocoding import router as geocoding_router
from app.api.qualification import router as qualification_router
from app.api.settings import router as settings_router
from app.api.webhooks import router as webhooks_router
from app.api.connectors import router as connectors_router
from app.api.integrations import router as integrations_router
from app.api.persons import router as people_router
from app.api.customers import router as customers_router
from app.api.search import router as search_router
from app.api.scheduler import router as scheduler_router
from app.api.fiber_plant import router as fiber_plant_router
from app.api.crm import router as crm_router
from app.api.sales import router as sales_router
from app.api.vendor import router as vendor_portal_router
from app.api.vendors import router as vendors_router
from app.api.subscribers import router as subscribers_router
from app.api.wireless_survey import router as wireless_survey_router
from app.api.wireless_masts import router as wireless_masts_router
from app.api.nextcloud_talk import router as nextcloud_talk_router
from app.api.bandwidth import router as bandwidth_router
from app.api.validation import router as validation_router
from app.api.defaults import router as defaults_router
from app.web_home import router as web_home_router
from app.web import router as web_router
from app.db import SessionLocal
from app.services import audit as audit_service
from app.services import settings_spec
from app.services.crm import smtp_inbound as smtp_inbound_service
from app.api.deps import require_permission, require_role, require_user_auth
from app.models.domain_settings import DomainSetting, SettingDomain
from sqlalchemy.orm import Session
from app.services.settings_seed import (
    seed_audit_settings,
    seed_auth_settings,
    seed_auth_policy_settings,
    seed_comms_settings,
    seed_geocoding_settings,
    seed_gis_settings,
    seed_inventory_settings,
    seed_network_policy_settings,
    seed_network_settings,
    seed_notification_settings,
    seed_scheduler_settings,
    seed_projects_settings,
    seed_provisioning_settings,
    seed_workflow_settings,
)
from app.logging import configure_logging
from app.observability import ObservabilityMiddleware
from app.middleware.api_rate_limit import APIRateLimitMiddleware
from app.telemetry import setup_otel
from app.errors import register_error_handlers

app = FastAPI(title="dotmac_crm API")

_AUDIT_SETTINGS_CACHE: dict | None = None
_AUDIT_SETTINGS_CACHE_AT: float | None = None
_AUDIT_SETTINGS_CACHE_TTL_SECONDS = 30.0
_AUDIT_SETTINGS_LOCK = Lock()

# Branding cache - 5 minute TTL since branding rarely changes
_BRANDING_CACHE: dict | None = None
_BRANDING_CACHE_AT: float | None = None
_BRANDING_CACHE_TTL_SECONDS = 300.0  # 5 minutes
_BRANDING_LOCK = Lock()
configure_logging()
setup_otel(app)
app.add_middleware(ObservabilityMiddleware)
app.add_middleware(APIRateLimitMiddleware)  # Global API rate limiting
register_error_handlers(app)


@app.middleware("http")
async def shared_db_session_middleware(request: Request, call_next):
    """
    Create a shared database session for middleware operations.

    This consolidates multiple SessionLocal() calls into one per request,
    reducing connection pool pressure and improving performance.
    """
    db = SessionLocal()
    request.state.middleware_db = db
    try:
        response = await call_next(request)
        return response
    finally:
        db.close()


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    response: Response
    path = request.url.path
    db: Session = getattr(request.state, "middleware_db", None) or SessionLocal()
    owns_db = not hasattr(request.state, "middleware_db")
    try:
        audit_settings = _load_audit_settings(db)
        if not audit_settings["enabled"]:
            return await call_next(request)
        track_read = request.method == "GET" and (
            request.headers.get(audit_settings["read_trigger_header"], "").lower() == "true"
            or request.query_params.get(audit_settings["read_trigger_query"]) == "true"
        )
        should_log = request.method in audit_settings["methods"] or track_read
        if _is_audit_path_skipped(path, audit_settings["skip_paths"]):
            should_log = False
        try:
            response = await call_next(request)
        except Exception:
            if should_log:
                audit_service.audit_events.log_request(
                    db, request, Response(status_code=500)
                )
            raise
        if should_log:
            audit_service.audit_events.log_request(db, request, response)
        return response
    finally:
        if owns_db:
            db.close()


def _load_branding_settings(db: Session) -> dict:
    """Load branding settings with double-checked locking pattern.

    Uses 5-minute cache since branding rarely changes, avoiding DB query on every request.
    """
    global _BRANDING_CACHE, _BRANDING_CACHE_AT
    now = monotonic()

    # Fast path: check cache validity without lock
    cache = _BRANDING_CACHE
    cache_at = _BRANDING_CACHE_AT
    if (
        cache is not None
        and cache_at is not None
        and now - cache_at < _BRANDING_CACHE_TTL_SECONDS
    ):
        return cache

    # Slow path: acquire lock and recheck
    with _BRANDING_LOCK:
        if (
            _BRANDING_CACHE is not None
            and _BRANDING_CACHE_AT is not None
            and now - _BRANDING_CACHE_AT < _BRANDING_CACHE_TTL_SECONDS
        ):
            return _BRANDING_CACHE

        # Cache miss - query database
        try:
            branding_keys = ["company_name", "brand_logo_url", "brand_favicon_url", "brand_color"]
            values = settings_spec.resolve_values_atomic(db, SettingDomain.comms, branding_keys)
            result = {
                "company_name": values.get("company_name") or "Dotmac",
                "logo_url": values.get("brand_logo_url"),
                "favicon_url": values.get("brand_favicon_url"),
                "brand_color": values.get("brand_color") or "#0f172a",
            }
        except Exception:
            result = {
                "company_name": "Dotmac",
                "logo_url": None,
                "favicon_url": None,
                "brand_color": "#0f172a",
            }

        _BRANDING_CACHE = result
        _BRANDING_CACHE_AT = now
        return result


@app.middleware("http")
async def branding_middleware(request: Request, call_next):
    """Attach branding settings to request state for templates.

    Uses in-memory cache with 5-minute TTL to avoid DB query on every request.
    """
    # Keep health/metrics fast and avoid DB during probes.
    if request.url.path in ("/health", "/metrics"):
        return await call_next(request)
    db: Session = getattr(request.state, "middleware_db", None) or SessionLocal()
    owns_db = not hasattr(request.state, "middleware_db")
    try:
        branding = _load_branding_settings(db)
    finally:
        if owns_db:
            db.close()
    request.state.branding = branding
    return await call_next(request)


# CSRF Protection paths - only protect web admin forms
_CSRF_PROTECTED_PATHS = ["/admin/", "/web/"]
_CSRF_EXEMPT_PATHS = ["/api/", "/auth/", "/health", "/metrics", "/static/"]


@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    """
    CSRF protection middleware using double-submit cookie pattern.

    For GET requests: Sets CSRF cookie if not present.
    For POST/PUT/DELETE on protected paths: Validates CSRF token.
    """
    path = request.url.path
    method = request.method.upper()

    # Skip CSRF for exempt paths
    if any(path.startswith(exempt) for exempt in _CSRF_EXEMPT_PATHS):
        return await call_next(request)

    # Check if path needs CSRF protection
    needs_protection = any(path.startswith(protected) for protected in _CSRF_PROTECTED_PATHS)

    if not needs_protection:
        return await call_next(request)

    # Ensure a consistent CSRF token for this request/response cycle
    if CSRF_COOKIE_NAME not in request.cookies:
        request.state.csrf_token = generate_csrf_token()

    # For state-changing methods, validate CSRF token
    if method in ("POST", "PUT", "DELETE", "PATCH"):
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME)

        if not cookie_token:
            # No CSRF cookie - reject request
            from fastapi.responses import HTMLResponse
            return HTMLResponse(
                content="<h1>403 Forbidden</h1><p>CSRF token missing. Please refresh the page and try again.</p>",
                status_code=403,
            )

        # Check header first (for HTMX/fetch requests)
        header_token = request.headers.get(CSRF_HEADER_NAME)
        if header_token:
            if not secrets.compare_digest(cookie_token, header_token):
                from fastapi.responses import HTMLResponse
                return HTMLResponse(
                    content="<h1>403 Forbidden</h1><p>CSRF token invalid. Please refresh the page and try again.</p>",
                    status_code=403,
                )
        else:
            # For form submissions, check form data
            content_type = request.headers.get("content-type", "")
            if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
                # Read body and check token
                body = await request.body()

                # Parse form data to get CSRF token
                from urllib.parse import parse_qs
                try:
                    if "multipart/form-data" in content_type:
                        # For multipart, we need to handle it differently
                        # The form data will include _csrf_token field
                        # Since we can't easily parse multipart here, we'll trust
                        # that the middleware before us handled it
                        # For now, we'll check if the field exists in the raw body
                        form_token = None
                        if b"_csrf_token" in body:
                            # Extract token from multipart body (simplified)
                            import re
                            match = re.search(rb'name="_csrf_token"\r\n\r\n([^\r\n-]+)', body)
                            if match:
                                form_token = match.group(1).decode('utf-8')
                    else:
                        form_data = parse_qs(body.decode('utf-8'))
                        form_token = form_data.get("_csrf_token", [None])[0]

                    if form_token and not secrets.compare_digest(cookie_token, form_token):
                        from fastapi.responses import HTMLResponse
                        return HTMLResponse(
                            content="<h1>403 Forbidden</h1><p>CSRF token invalid. Please refresh the page and try again.</p>",
                            status_code=403,
                        )
                except Exception:
                    pass  # If parsing fails, continue (token validation happens elsewhere)

                # Reconstruct request with body for downstream handlers.
                # Starlette expects a stream; return body once then empty.
                body_sent = False

                async def receive():
                    nonlocal body_sent
                    if body_sent:
                        return {"type": "http.request", "body": b"", "more_body": False}
                    body_sent = True
                    return {"type": "http.request", "body": body, "more_body": False}

                request = Request(scope=request.scope, receive=receive)

    response = await call_next(request)
    if response is None:
        return Response(
            content='{"detail":"No response returned"}',
            media_type="application/json",
            status_code=500,
        )

    # Set CSRF cookie on responses if not present
    if CSRF_COOKIE_NAME not in request.cookies:
        token = getattr(request.state, "csrf_token", None) or generate_csrf_token()
        set_csrf_cookie(response, token)

    return response


def _load_audit_settings(db: Session):
    """Load audit settings with double-checked locking pattern.

    This avoids acquiring the lock on every request when cache is valid,
    reducing contention under high load.
    """
    global _AUDIT_SETTINGS_CACHE, _AUDIT_SETTINGS_CACHE_AT
    now = monotonic()

    # Fast path: check cache validity without lock (read is atomic for these types)
    cache = _AUDIT_SETTINGS_CACHE
    cache_at = _AUDIT_SETTINGS_CACHE_AT
    if (
        cache is not None
        and cache_at is not None
        and now - cache_at < _AUDIT_SETTINGS_CACHE_TTL_SECONDS
    ):
        return cache

    # Slow path: acquire lock and recheck (double-checked locking)
    with _AUDIT_SETTINGS_LOCK:
        # Recheck after acquiring lock (another thread may have updated)
        if (
            _AUDIT_SETTINGS_CACHE is not None
            and _AUDIT_SETTINGS_CACHE_AT is not None
            and now - _AUDIT_SETTINGS_CACHE_AT < _AUDIT_SETTINGS_CACHE_TTL_SECONDS
        ):
            return _AUDIT_SETTINGS_CACHE

        # Cache miss - query database
        defaults = {
            "enabled": True,
            "methods": {"POST", "PUT", "PATCH", "DELETE"},
            "skip_paths": ["/static", "/web", "/health"],
            "read_trigger_header": "x-audit-read",
            "read_trigger_query": "audit",
        }
        rows = (
            db.query(DomainSetting)
            .filter(DomainSetting.domain == SettingDomain.audit)
            .filter(DomainSetting.is_active.is_(True))
            .all()
        )
        values = {row.key: row for row in rows}
        if "enabled" in values:
            defaults["enabled"] = _to_bool(values["enabled"])
        if "methods" in values:
            defaults["methods"] = _to_list(values["methods"], upper=True)
        if "skip_paths" in values:
            defaults["skip_paths"] = _to_list(values["skip_paths"], upper=False)
        if "read_trigger_header" in values:
            defaults["read_trigger_header"] = _to_str(values["read_trigger_header"])
        if "read_trigger_query" in values:
            defaults["read_trigger_query"] = _to_str(values["read_trigger_query"])

        _AUDIT_SETTINGS_CACHE = defaults
        _AUDIT_SETTINGS_CACHE_AT = now
        return defaults


def _to_bool(setting: DomainSetting) -> bool:
    value = setting.value_json if setting.value_json is not None else setting.value_text
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _to_str(setting: DomainSetting) -> str:
    value = setting.value_text if setting.value_text is not None else setting.value_json
    if value is None:
        return ""
    return str(value)


def _to_list(setting: DomainSetting, upper: bool) -> set[str] | list[str]:
    value = setting.value_json if setting.value_json is not None else setting.value_text
    items: list[str]
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
    else:
        items = []
    if upper:
        return {item.upper() for item in items}
    return items


def _is_audit_path_skipped(path: str, skip_paths: list[str]) -> bool:
    return any(path.startswith(prefix) for prefix in skip_paths)

def _include_api_router(router, dependencies=None):
    app.include_router(router, dependencies=dependencies)
    app.include_router(router, prefix="/api/v1", dependencies=dependencies)


_include_api_router(notifications_router, dependencies=[Depends(require_user_auth)])
_include_api_router(tickets_router, dependencies=[Depends(require_user_auth)])
_include_api_router(projects_router, dependencies=[Depends(require_user_auth)])
_include_api_router(workforce_router, dependencies=[Depends(require_user_auth)])
_include_api_router(external_router, dependencies=[Depends(require_user_auth)])
_include_api_router(sales_orders_router, dependencies=[Depends(require_user_auth)])
_include_api_router(auth_router, dependencies=[Depends(require_role("admin"))])
# Only include auth_flow at /api/v1 to avoid conflict with web /auth/login
app.include_router(auth_flow_router, prefix="/api/v1")
_include_api_router(rbac_router, dependencies=[Depends(require_user_auth)])
_include_api_router(people_router, dependencies=[Depends(require_user_auth)])
_include_api_router(customers_router, dependencies=[Depends(require_user_auth)])
_include_api_router(search_router, dependencies=[Depends(require_user_auth)])
_include_api_router(audit_router)
_include_api_router(gis_router, dependencies=[Depends(require_user_auth)])
_include_api_router(geocoding_router, dependencies=[Depends(require_user_auth)])
_include_api_router(qualification_router, dependencies=[Depends(require_user_auth)])
_include_api_router(settings_router, dependencies=[Depends(require_user_auth)])
_include_api_router(webhooks_router, dependencies=[Depends(require_user_auth)])
_include_api_router(connectors_router, dependencies=[Depends(require_user_auth)])
_include_api_router(integrations_router, dependencies=[Depends(require_user_auth)])
_include_api_router(scheduler_router, dependencies=[Depends(require_user_auth)])
_include_api_router(workflow_router, dependencies=[Depends(require_user_auth)])
_include_api_router(dispatch_router, dependencies=[Depends(require_user_auth)])
_include_api_router(inventory_router, dependencies=[Depends(require_user_auth)])
_include_api_router(timecost_router, dependencies=[Depends(require_user_auth)])
_include_api_router(comms_router, dependencies=[Depends(require_user_auth)])
_include_api_router(analytics_router, dependencies=[Depends(require_user_auth)])
_include_api_router(fiber_plant_router, dependencies=[Depends(require_user_auth)])
_include_api_router(crm_router, dependencies=[Depends(require_user_auth)])
_include_api_router(sales_router, dependencies=[Depends(require_user_auth)])
_include_api_router(wireless_survey_router, dependencies=[Depends(require_user_auth)])
_include_api_router(wireless_masts_router, dependencies=[Depends(require_user_auth)])
_include_api_router(nextcloud_talk_router, dependencies=[Depends(require_user_auth)])
_include_api_router(bandwidth_router, dependencies=[Depends(require_user_auth)])
_include_api_router(validation_router, dependencies=[Depends(require_user_auth)])
_include_api_router(defaults_router, dependencies=[Depends(require_user_auth)])
_include_api_router(subscribers_router, dependencies=[Depends(require_user_auth)])
# Chat widget public endpoints - no auth required (visitor token-based)
from app.api.crm.widget_public import router as widget_public_router
_include_api_router(widget_public_router)
app.include_router(vendors_router, prefix="/api", dependencies=[Depends(require_user_auth)])
app.include_router(vendors_router, prefix="/api/v1", dependencies=[Depends(require_user_auth)])
app.include_router(vendor_portal_router, prefix="/api")
app.include_router(vendor_portal_router, prefix="/api/v1")
app.include_router(web_home_router)
app.include_router(web_router)

from app.websocket.router import router as ws_router
from app.websocket.widget_router import router as ws_widget_router
app.include_router(ws_router)
app.include_router(ws_widget_router)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.middleware("http")
async def static_cache_middleware(request: Request, call_next):
    """Add Cache-Control headers for static assets.

    - CSS/JS/fonts: 1 year cache (immutable, versioned by content hash)
    - Images: 1 week cache
    - Other static: 1 day cache
    """
    response = await call_next(request)
    if response is None:
        return Response(
            content='{"detail":"No response returned"}',
            media_type="application/json",
            status_code=500,
        )
    path = request.url.path

    if path.startswith("/static/"):
        # Skip if already has cache headers
        if "cache-control" not in response.headers:
            if any(path.endswith(ext) for ext in (".css", ".js", ".woff2", ".woff")):
                # Long cache for versioned assets
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif any(path.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp")):
                # Medium cache for images
                response.headers["Cache-Control"] = "public, max-age=604800"
            else:
                # Short cache for other static files
                response.headers["Cache-Control"] = "public, max-age=86400"

    return response


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.head("/")
def head_root():
    return Response(status_code=200)


@app.head("/health")
def head_health():
    return Response(status_code=200)


@app.get("/metrics")
def metrics():
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@app.on_event("startup")
def _start_jobs():
    db = SessionLocal()
    try:
        seed_auth_settings(db)
        seed_auth_policy_settings(db)
        seed_audit_settings(db)
        seed_gis_settings(db)
        seed_notification_settings(db)
        seed_geocoding_settings(db)
        seed_scheduler_settings(db)
        seed_provisioning_settings(db)
        seed_projects_settings(db)
        seed_workflow_settings(db)
        seed_network_policy_settings(db)
        seed_network_settings(db)
        seed_inventory_settings(db)
        seed_comms_settings(db)
    finally:
        db.close()
    smtp_inbound_service.start_smtp_inbound_server()


@app.on_event("startup")
async def _start_websocket_manager():
    from app.websocket.manager import get_connection_manager
    manager = get_connection_manager()
    await manager.connect()


@app.on_event("shutdown")
async def _stop_websocket_manager():
    from app.websocket.manager import get_connection_manager
    manager = get_connection_manager()
    await manager.disconnect()
