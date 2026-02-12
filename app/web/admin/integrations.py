"""Admin integrations routes."""

import json
from datetime import UTC, datetime
from html import escape as html_escape
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.connector import ConnectorConfig
from app.models.domain_settings import DomainSetting, SettingDomain, SettingValueType
from app.schemas.connector import ConnectorConfigCreate
from app.schemas.integration import IntegrationJobCreate, IntegrationTargetCreate
from app.schemas.webhook import WebhookEndpointCreate, WebhookSubscriptionCreate
from app.services import connector as connector_service
from app.services import integration as integration_service
from app.services import webhook as webhook_service
from app.services.audit_helpers import recent_activity_for_paths

router = APIRouter(prefix="/integrations", tags=["web-admin-integrations"])
templates = Jinja2Templates(directory="templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _base_context(request: Request, db: Session, active_page: str, active_menu: str = "integrations") -> dict:
    """Build base template context."""
    from app.web.admin import get_current_user, get_sidebar_stats

    return {
        "request": request,
        "active_page": active_page,
        "active_menu": active_menu,
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
    }


def _parse_uuid(value: str | None, field: str, required: bool = True) -> UUID | None:
    if not value:
        if required:
            raise ValueError(f"{field} is required")
        return None
    return UUID(value)


def _parse_json(value: str | None, field: str) -> dict | None:
    if not value or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{field} must be a JSON object")
    return parsed


def _load_payment_providers(db: Session) -> tuple[list[dict], DomainSetting | None]:
    setting = (
        db.query(DomainSetting)
        .filter(DomainSetting.domain == SettingDomain.billing)
        .filter(DomainSetting.key == "payment_providers")
        .first()
    )
    if not setting or not setting.is_active:
        return [], setting
    value = setting.value_json or {}
    if isinstance(value, list):
        providers = value
    elif isinstance(value, dict):
        providers = value.get("providers") or []
    else:
        providers = []
    return providers, setting


def _save_payment_providers(db: Session, providers: list[dict], setting: DomainSetting | None) -> None:
    if not setting:
        setting = DomainSetting(
            domain=SettingDomain.billing,
            key="payment_providers",
            value_type=SettingValueType.json,
            value_json={"providers": providers},
            value_text=None,
            is_active=True,
        )
        db.add(setting)
    else:
        setting.value_type = SettingValueType.json
        setting.value_json = {"providers": providers}
        setting.value_text = None
        setting.is_active = True
    db.commit()


def _provider_view(provider: dict, connector: ConnectorConfig | None):
    provider_type = provider.get("provider_type") or "custom"
    return SimpleNamespace(
        id=provider.get("id"),
        name=provider.get("name"),
        provider_type=SimpleNamespace(value=provider_type),
        connector_config=connector,
        is_active=provider.get("is_active", True),
        webhook_secret_ref=provider.get("webhook_secret_ref"),
        notes=provider.get("notes"),
        created_at=provider.get("created_at"),
        updated_at=provider.get("updated_at"),
    )


# ==================== Connectors ====================


@router.get("/connectors", response_class=HTMLResponse)
def connectors_list(request: Request, db: Session = Depends(get_db)):
    """List all connector configurations."""
    connectors = connector_service.connector_configs.list_all(
        db=db,
        connector_type=None,
        auth_type=None,
        order_by="name",
        order_dir="asc",
        limit=100,
        offset=0,
    )

    stats_by_type: dict[str, int] = {}
    stats = {
        "total": len(connectors),
        "active": sum(1 for c in connectors if c.is_active),
        "by_type": stats_by_type,
    }

    for c in connectors:
        t = c.connector_type.value if hasattr(c.connector_type, "value") else str(c.connector_type or "custom")
        stats_by_type[t] = stats_by_type.get(t, 0) + 1

    context = _base_context(request, db, active_page="connectors")
    context.update(
        {
            "connectors": connectors,
            "stats": stats,
            "recent_activities": recent_activity_for_paths(db, ["/admin/integrations"]),
        }
    )
    return templates.TemplateResponse("admin/integrations/connectors/index.html", context)


@router.get("/connectors/new", response_class=HTMLResponse)
def connector_new(request: Request, db: Session = Depends(get_db)):
    """New connector form."""
    from app.models.connector import ConnectorAuthType, ConnectorType

    context = _base_context(request, db, active_page="connectors")
    context.update(
        {
            "connector_types": [t.value for t in ConnectorType],
            "auth_types": [t.value for t in ConnectorAuthType],
        }
    )
    return templates.TemplateResponse("admin/integrations/connectors/new.html", context)


@router.post("/connectors", response_class=HTMLResponse)
def connector_create(
    request: Request,
    name: str = Form(...),
    connector_type: str = Form("custom"),
    auth_type: str = Form("none"),
    base_url: str | None = Form(None),
    timeout_sec: str | None = Form(None),
    auth_config: str | None = Form(None),
    headers: str | None = Form(None),
    retry_policy: str | None = Form(None),
    metadata: str | None = Form(None),
    notes: str | None = Form(None),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    try:
        from app.models.connector import ConnectorAuthType, ConnectorType

        payload = ConnectorConfigCreate(
            name=name.strip(),
            connector_type=ConnectorType(connector_type),
            auth_type=ConnectorAuthType(auth_type),
            base_url=base_url.strip() if base_url else None,
            timeout_sec=int(timeout_sec) if timeout_sec else None,
            auth_config=_parse_json(auth_config, "auth_config"),
            headers=_parse_json(headers, "headers"),
            retry_policy=_parse_json(retry_policy, "retry_policy"),
            metadata_=_parse_json(metadata, "metadata"),
            notes=notes.strip() if notes else None,
            is_active=is_active,
        )
        connector = connector_service.connector_configs.create(db, payload)
    except Exception as exc:
        from app.models.connector import ConnectorAuthType, ConnectorType

        context = _base_context(request, db, active_page="connectors")
        context.update(
            {
                "connector_types": [t.value for t in ConnectorType],
                "auth_types": [t.value for t in ConnectorAuthType],
                "error": str(exc),
                "form": {
                    "name": name,
                    "connector_type": connector_type,
                    "auth_type": auth_type,
                    "base_url": base_url or "",
                    "timeout_sec": timeout_sec or "",
                    "auth_config": auth_config or "",
                    "headers": headers or "",
                    "retry_policy": retry_policy or "",
                    "metadata": metadata or "",
                    "notes": notes or "",
                    "is_active": is_active,
                },
            }
        )
        return templates.TemplateResponse("admin/integrations/connectors/new.html", context, status_code=400)
    return RedirectResponse(url=f"/admin/integrations/connectors/{connector.id}", status_code=303)


@router.get("/connectors/{connector_id}", response_class=HTMLResponse)
def connector_detail(request: Request, connector_id: str, db: Session = Depends(get_db)):
    """Connector detail view."""
    try:
        connector = connector_service.connector_configs.get(db, connector_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        context = _base_context(request, db, active_page="connectors")
        context["message"] = "The connector you are looking for does not exist."
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)

    context = _base_context(request, db, active_page="connectors")
    context.update({"connector": connector})
    return templates.TemplateResponse("admin/integrations/connectors/detail.html", context)


# ==================== Integration Targets ====================


@router.get("/targets", response_class=HTMLResponse)
def targets_list(request: Request, db: Session = Depends(get_db)):
    """List all integration targets."""
    targets = integration_service.integration_targets.list_all(
        db=db,
        target_type=None,
        order_by="name",
        order_dir="asc",
        limit=100,
        offset=0,
    )

    stats_by_type: dict[str, int] = {}
    stats = {
        "total": len(targets),
        "active": sum(1 for t in targets if t.is_active),
        "by_type": stats_by_type,
    }

    for t in targets:
        tt = t.target_type.value if hasattr(t.target_type, "value") else str(t.target_type or "custom")
        stats_by_type[tt] = stats_by_type.get(tt, 0) + 1

    context = _base_context(request, db, active_page="targets")
    context.update(
        {
            "targets": targets,
            "stats": stats,
            "recent_activities": recent_activity_for_paths(db, ["/admin/integrations"]),
        }
    )
    return templates.TemplateResponse("admin/integrations/targets/index.html", context)


@router.get("/targets/new", response_class=HTMLResponse)
def target_new(request: Request, db: Session = Depends(get_db)):
    """New target form."""
    from app.models.integration import IntegrationTargetType

    connectors = connector_service.connector_configs.list(
        db=db,
        connector_type=None,
        auth_type=None,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=1000,
        offset=0,
    )

    context = _base_context(request, db, active_page="targets")
    context.update(
        {
            "target_types": [t.value for t in IntegrationTargetType],
            "connectors": connectors,
        }
    )
    return templates.TemplateResponse("admin/integrations/targets/new.html", context)


@router.post("/targets", response_class=HTMLResponse)
def target_create(
    request: Request,
    name: str = Form(...),
    target_type: str = Form("custom"),
    connector_config_id: str | None = Form(None),
    notes: str | None = Form(None),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    try:
        from app.models.integration import IntegrationTargetType

        payload = IntegrationTargetCreate(
            name=name.strip(),
            target_type=IntegrationTargetType(target_type),
            connector_config_id=_parse_uuid(connector_config_id, "connector_config_id", required=False),
            notes=notes.strip() if notes else None,
            is_active=is_active,
        )
        target = integration_service.integration_targets.create(db, payload)
    except Exception as exc:
        from app.models.integration import IntegrationTargetType

        connectors = connector_service.connector_configs.list(
            db=db,
            connector_type=None,
            auth_type=None,
            is_active=True,
            order_by="name",
            order_dir="asc",
            limit=1000,
            offset=0,
        )
        context = _base_context(request, db, active_page="targets")
        context.update(
            {
                "target_types": [t.value for t in IntegrationTargetType],
                "connectors": connectors,
                "error": str(exc),
                "form": {
                    "name": name,
                    "target_type": target_type,
                    "connector_config_id": connector_config_id or "",
                    "notes": notes or "",
                    "is_active": is_active,
                },
            }
        )
        return templates.TemplateResponse("admin/integrations/targets/new.html", context, status_code=400)
    return RedirectResponse(url=f"/admin/integrations/targets/{target.id}", status_code=303)


@router.get("/targets/{target_id}", response_class=HTMLResponse)
def target_detail(request: Request, target_id: str, db: Session = Depends(get_db)):
    """Target detail view."""
    try:
        target = integration_service.integration_targets.get(db, target_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        context = _base_context(request, db, active_page="targets")
        context["message"] = "The integration target you are looking for does not exist."
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)

    context = _base_context(request, db, active_page="targets")
    context.update({"target": target})
    return templates.TemplateResponse("admin/integrations/targets/detail.html", context)


# ==================== Integration Jobs ====================


@router.get("/jobs", response_class=HTMLResponse)
def jobs_list(request: Request, db: Session = Depends(get_db)):
    """List all integration jobs."""
    jobs = integration_service.integration_jobs.list_all(
        db=db,
        target_id=None,
        job_type=None,
        schedule_type=None,
        order_by="name",
        order_dir="asc",
        limit=100,
        offset=0,
    )

    # Get recent runs for each job
    job_runs = {}
    for job in jobs:
        recent_runs = integration_service.integration_runs.list(
            db=db,
            job_id=str(job.id),
            status=None,
            order_by="started_at",
            order_dir="desc",
            limit=5,
            offset=0,
        )
        job_runs[str(job.id)] = recent_runs

    def _schedule_value(item):
        schedule = getattr(item, "schedule_type", None)
        if hasattr(schedule, "value"):
            return schedule.value
        return str(schedule) if schedule else None

    stats = {
        "total": len(jobs),
        "active": sum(1 for j in jobs if j.is_active),
        "manual": sum(1 for j in jobs if _schedule_value(j) == "manual"),
        "scheduled": sum(1 for j in jobs if _schedule_value(j) == "interval"),
    }

    context = _base_context(request, db, active_page="jobs")
    context.update(
        {
            "jobs": jobs,
            "job_runs": job_runs,
            "stats": stats,
            "recent_activities": recent_activity_for_paths(db, ["/admin/integrations"]),
        }
    )
    return templates.TemplateResponse("admin/integrations/jobs/index.html", context)


@router.get("/jobs/new", response_class=HTMLResponse)
def job_new(request: Request, db: Session = Depends(get_db)):
    """New job form."""
    from app.models.integration import IntegrationJobType, IntegrationScheduleType

    targets = integration_service.integration_targets.list(
        db=db,
        target_type=None,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=1000,
        offset=0,
    )

    context = _base_context(request, db, active_page="jobs")
    context.update(
        {
            "job_types": [t.value for t in IntegrationJobType],
            "schedule_types": [t.value for t in IntegrationScheduleType],
            "targets": targets,
        }
    )
    return templates.TemplateResponse("admin/integrations/jobs/new.html", context)


@router.post("/jobs", response_class=HTMLResponse)
def job_create(
    request: Request,
    target_id: str = Form(...),
    name: str = Form(...),
    job_type: str = Form("sync"),
    schedule_type: str = Form("manual"),
    interval_minutes: str | None = Form(None),
    notes: str | None = Form(None),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    try:
        interval_value = int(interval_minutes) if interval_minutes else None
        if schedule_type == "interval" and not interval_value:
            raise ValueError("interval_minutes is required for interval schedules")
        from app.models.integration import IntegrationJobType, IntegrationScheduleType

        target_uuid = _parse_uuid(target_id, "target_id")
        if target_uuid is None:
            raise ValueError("target_id is required")
        payload = IntegrationJobCreate(
            target_id=target_uuid,
            name=name.strip(),
            job_type=IntegrationJobType(job_type),
            schedule_type=IntegrationScheduleType(schedule_type),
            interval_minutes=interval_value,
            notes=notes.strip() if notes else None,
            is_active=is_active,
        )
        job = integration_service.integration_jobs.create(db, payload)
    except Exception as exc:
        from app.models.integration import IntegrationJobType, IntegrationScheduleType

        targets = integration_service.integration_targets.list(
            db=db,
            target_type=None,
            is_active=True,
            order_by="name",
            order_dir="asc",
            limit=1000,
            offset=0,
        )
        context = _base_context(request, db, active_page="jobs")
        context.update(
            {
                "job_types": [t.value for t in IntegrationJobType],
                "schedule_types": [t.value for t in IntegrationScheduleType],
                "targets": targets,
                "error": str(exc),
                "form": {
                    "target_id": target_id,
                    "name": name,
                    "job_type": job_type,
                    "schedule_type": schedule_type,
                    "interval_minutes": interval_minutes or "",
                    "notes": notes or "",
                    "is_active": is_active,
                },
            }
        )
        return templates.TemplateResponse("admin/integrations/jobs/new.html", context, status_code=400)
    return RedirectResponse(url=f"/admin/integrations/jobs/{job.id}", status_code=303)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str, db: Session = Depends(get_db)):
    """Job detail view with run history."""
    try:
        job = integration_service.integration_jobs.get(db, job_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        context = _base_context(request, db, active_page="jobs")
        context["message"] = "The integration job you are looking for does not exist."
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)

    runs = integration_service.integration_runs.list(
        db=db,
        job_id=str(job.id),
        status=None,
        order_by="started_at",
        order_dir="desc",
        limit=50,
        offset=0,
    )

    context = _base_context(request, db, active_page="jobs")
    context.update({"job": job, "runs": runs})
    return templates.TemplateResponse("admin/integrations/jobs/detail.html", context)


# ==================== Webhooks ====================


@router.get("/webhooks", response_class=HTMLResponse)
def webhooks_list(request: Request, db: Session = Depends(get_db)):
    """List all webhook endpoints."""
    endpoints = webhook_service.webhook_endpoints.list_all(
        db=db,
        order_by="name",
        order_dir="asc",
        limit=100,
        offset=0,
    )

    # Get subscriptions and delivery counts
    endpoint_stats = {}
    for endpoint in endpoints:
        subs = webhook_service.webhook_subscriptions.list_all(
            db=db,
            endpoint_id=str(endpoint.id),
            event_type=None,
            order_by="created_at",
            order_dir="desc",
            limit=1000,
            offset=0,
        )
        pending = webhook_service.webhook_deliveries.list(
            db=db,
            endpoint_id=str(endpoint.id),
            subscription_id=None,
            event_type=None,
            status="pending",
            order_by="created_at",
            order_dir="desc",
            limit=1000,
            offset=0,
        )
        failed = webhook_service.webhook_deliveries.list(
            db=db,
            endpoint_id=str(endpoint.id),
            subscription_id=None,
            event_type=None,
            status="failed",
            order_by="created_at",
            order_dir="desc",
            limit=1000,
            offset=0,
        )
        endpoint_stats[str(endpoint.id)] = {
            "subscriptions": len(subs),
            "pending": len(pending),
            "failed": len(failed),
        }

    stats = {
        "total": len(endpoints),
        "active": sum(1 for e in endpoints if e.is_active),
    }

    context = _base_context(request, db, active_page="webhooks")
    context.update(
        {
            "endpoints": endpoints,
            "endpoint_stats": endpoint_stats,
            "stats": stats,
            "recent_activities": recent_activity_for_paths(db, ["/admin/integrations"]),
        }
    )
    return templates.TemplateResponse("admin/integrations/webhooks/index.html", context)


@router.get("/webhooks/new", response_class=HTMLResponse)
def webhook_new(request: Request, db: Session = Depends(get_db)):
    """New webhook endpoint form."""
    from app.models.webhook import WebhookEventType

    connectors = connector_service.connector_configs.list(
        db=db,
        connector_type=None,
        auth_type=None,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=1000,
        offset=0,
    )

    context = _base_context(request, db, active_page="webhooks")
    context.update(
        {
            "event_types": [t.value for t in WebhookEventType],
            "connectors": connectors,
        }
    )
    return templates.TemplateResponse("admin/integrations/webhooks/new.html", context)


@router.post("/webhooks", response_class=HTMLResponse)
def webhook_create(
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    connector_config_id: str | None = Form(None),
    secret: str | None = Form(None),
    event_types: list[str] | None = Form(None),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    try:
        from app.models.webhook import WebhookEventType

        payload = WebhookEndpointCreate(
            name=name.strip(),
            url=url.strip(),
            connector_config_id=_parse_uuid(connector_config_id, "connector_config_id", required=False),
            secret=secret.strip() if secret else None,
            is_active=is_active,
        )
        endpoint = webhook_service.webhook_endpoints.create(db, payload)
        for event_type in event_types or []:
            subscription_payload = WebhookSubscriptionCreate(
                endpoint_id=endpoint.id,
                event_type=WebhookEventType(event_type),
                is_active=True,
            )
            webhook_service.webhook_subscriptions.create(db, subscription_payload)
    except Exception as exc:
        from app.models.webhook import WebhookEventType

        connectors = connector_service.connector_configs.list(
            db=db,
            connector_type=None,
            auth_type=None,
            is_active=True,
            order_by="name",
            order_dir="asc",
            limit=1000,
            offset=0,
        )
        context = _base_context(request, db, active_page="webhooks")
        context.update(
            {
                "event_types": [t.value for t in WebhookEventType],
                "connectors": connectors,
                "error": str(exc),
                "form": {
                    "name": name,
                    "url": url,
                    "connector_config_id": connector_config_id or "",
                    "secret": secret or "",
                    "event_types": event_types or [],
                    "is_active": is_active,
                },
            }
        )
        return templates.TemplateResponse("admin/integrations/webhooks/new.html", context, status_code=400)
    return RedirectResponse(url=f"/admin/integrations/webhooks/{endpoint.id}", status_code=303)


@router.get("/webhooks/{endpoint_id}", response_class=HTMLResponse)
def webhook_detail(request: Request, endpoint_id: str, db: Session = Depends(get_db)):
    """Webhook endpoint detail view."""
    try:
        endpoint = webhook_service.webhook_endpoints.get(db, endpoint_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        context = _base_context(request, db, active_page="webhooks")
        context["message"] = "The webhook endpoint you are looking for does not exist."
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)

    subscriptions = webhook_service.webhook_subscriptions.list_all(
        db=db,
        endpoint_id=str(endpoint.id),
        event_type=None,
        order_by="created_at",
        order_dir="desc",
        limit=1000,
        offset=0,
    )

    deliveries = webhook_service.webhook_deliveries.list(
        db=db,
        endpoint_id=str(endpoint.id),
        subscription_id=None,
        event_type=None,
        status=None,
        order_by="created_at",
        order_dir="desc",
        limit=50,
        offset=0,
    )

    context = _base_context(request, db, active_page="webhooks")
    context.update(
        {
            "endpoint": endpoint,
            "subscriptions": subscriptions,
            "deliveries": deliveries,
        }
    )
    return templates.TemplateResponse("admin/integrations/webhooks/detail.html", context)


# ==================== Payment Providers ====================


@router.get("/providers", response_class=HTMLResponse)
def providers_list(request: Request, db: Session = Depends(get_db)):
    providers_raw, _setting = _load_payment_providers(db)
    connectors = connector_service.connector_configs.list_all(
        db=db,
        connector_type=None,
        auth_type=None,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    connector_by_id = {str(c.id): c for c in connectors}

    providers = [_provider_view(p, connector_by_id.get(str(p.get("connector_config_id")))) for p in providers_raw]

    stats_by_type: dict[str, int] = {}
    stats = {
        "total": len(providers),
        "active": sum(1 for p in providers if p.is_active),
        "by_type": stats_by_type,
    }
    for p in providers:
        ptype = p.provider_type.value if p.provider_type else "custom"
        stats_by_type[ptype] = stats_by_type.get(ptype, 0) + 1

    context = _base_context(request, db, active_page="providers")
    context.update(
        {
            "providers": providers,
            "stats": stats,
            "recent_activities": recent_activity_for_paths(db, ["/admin/integrations"]),
        }
    )
    return templates.TemplateResponse("admin/integrations/providers/index.html", context)


@router.get("/providers/new", response_class=HTMLResponse)
def provider_new(request: Request, db: Session = Depends(get_db)):
    connectors = connector_service.connector_configs.list_all(
        db=db,
        connector_type=None,
        auth_type=None,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    context = _base_context(request, db, active_page="providers")
    context.update(
        {
            "provider_types": ["stripe", "paypal", "manual", "custom"],
            "connectors": connectors,
        }
    )
    return templates.TemplateResponse("admin/integrations/providers/new.html", context)


@router.post("/providers", response_class=HTMLResponse)
def provider_create(
    request: Request,
    name: str = Form(...),
    provider_type: str = Form("custom"),
    connector_config_id: str | None = Form(None),
    webhook_secret_ref: str | None = Form(None),
    notes: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    connectors = connector_service.connector_configs.list_all(
        db=db,
        connector_type=None,
        auth_type=None,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    provider_types = ["stripe", "paypal", "manual", "custom"]

    try:
        if provider_type not in provider_types:
            raise ValueError("Invalid provider type")
        connector_id = connector_config_id or None
        if connector_id and not db.get(ConnectorConfig, UUID(connector_id)):
            raise ValueError("Connector not found")
    except (ValueError, HTTPException) as exc:
        context = _base_context(request, db, active_page="providers")
        context.update(
            {
                "provider_types": provider_types,
                "connectors": connectors,
                "error": str(exc),
                "form": {
                    "name": name,
                    "provider_type": provider_type,
                    "connector_config_id": connector_config_id,
                    "webhook_secret_ref": webhook_secret_ref,
                    "notes": notes,
                    "is_active": bool(is_active),
                },
            }
        )
        return templates.TemplateResponse("admin/integrations/providers/new.html", context, status_code=400)

    providers, setting = _load_payment_providers(db)
    now = datetime.now(UTC)
    provider_id = str(uuid4())
    providers.append(
        {
            "id": provider_id,
            "name": name,
            "provider_type": provider_type,
            "connector_config_id": connector_id,
            "webhook_secret_ref": webhook_secret_ref,
            "notes": notes,
            "is_active": bool(is_active) if is_active is not None else True,
            "created_at": now,
            "updated_at": now,
        }
    )
    _save_payment_providers(db, providers, setting)
    return RedirectResponse(url=f"/admin/integrations/providers/{provider_id}", status_code=303)


@router.get("/providers/{provider_id}", response_class=HTMLResponse)
def provider_detail(provider_id: str, request: Request, db: Session = Depends(get_db)):
    providers, _setting = _load_payment_providers(db)
    provider = next((p for p in providers if str(p.get("id")) == str(provider_id)), None)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    connector = None
    connector_id = provider.get("connector_config_id")
    if connector_id:
        connector = db.get(ConnectorConfig, UUID(connector_id))
    view = _provider_view(provider, connector)

    context = _base_context(request, db, active_page="providers")
    context.update(
        {
            "provider": view,
            "events": [],
        }
    )
    return templates.TemplateResponse("admin/integrations/providers/detail.html", context)


# ==================== CRM Channels ====================


@router.get("/channels", response_class=HTMLResponse)
def channels_list(request: Request, db: Session = Depends(get_db)):
    """List all CRM team channels."""
    from app.models.crm.enums import ChannelType
    from app.services.crm import team as crm_team_service

    channels = crm_team_service.TeamChannels.list(
        db=db,
        team_id=None,
        channel_type=None,
        order_by="created_at",
        order_dir="desc",
        limit=100,
        offset=0,
    )

    # Get teams for display
    teams = crm_team_service.Teams.list(
        db=db,
        is_active=None,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    team_map = {str(team.id): team for team in teams}

    stats_by_type: dict[str, int] = {}
    stats = {
        "total": len(channels),
        "active": sum(1 for c in channels if c.is_active),
        "by_type": stats_by_type,
    }
    for channel in channels:
        channel_type = channel.channel_type.value if channel.channel_type else "unknown"
        stats_by_type[channel_type] = stats_by_type.get(channel_type, 0) + 1

    context = _base_context(request, db, active_page="channels")
    context.update(
        {
            "channels": channels,
            "team_map": team_map,
            "stats": stats,
            "channel_types": [t.value for t in ChannelType],
            "recent_activities": recent_activity_for_paths(db, ["/admin/integrations/channels"]),
        }
    )
    return templates.TemplateResponse("admin/integrations/channels/index.html", context)


@router.get("/channels/new", response_class=HTMLResponse)
def channel_new(request: Request, db: Session = Depends(get_db)):
    """New channel form."""
    from app.models.crm.enums import ChannelType
    from app.services.crm import team as crm_team_service

    teams = crm_team_service.Teams.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )

    targets = integration_service.integration_targets.list(
        db=db,
        target_type=None,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )

    context = _base_context(request, db, active_page="channels")
    context.update(
        {
            "channel_types": [t.value for t in ChannelType],
            "teams": teams,
            "targets": targets,
        }
    )
    return templates.TemplateResponse("admin/integrations/channels/new.html", context)


@router.post("/channels", response_class=HTMLResponse)
def channel_create(
    request: Request,
    team_id: str = Form(...),
    channel_type: str = Form(...),
    channel_target_id: str | None = Form(None),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Create a new CRM channel."""
    from pydantic import BaseModel

    from app.models.crm.enums import ChannelType
    from app.services.crm import team as crm_team_service

    class ChannelCreate(BaseModel):
        team_id: UUID
        channel_type: ChannelType
        channel_target_id: UUID | None = None
        is_active: bool = True

    try:
        team_uuid = _parse_uuid(team_id, "team_id")
        if team_uuid is None:
            raise ValueError("team_id is required")
        payload = ChannelCreate(
            team_id=team_uuid,
            channel_type=ChannelType(channel_type),
            channel_target_id=_parse_uuid(channel_target_id, "channel_target_id", required=False),
            is_active=is_active,
        )
        channel = crm_team_service.TeamChannels.create(db, payload)
    except Exception as exc:
        teams = crm_team_service.Teams.list(
            db=db,
            is_active=True,
            order_by="name",
            order_dir="asc",
            limit=200,
            offset=0,
        )
        targets = integration_service.integration_targets.list(
            db=db,
            target_type=None,
            is_active=True,
            order_by="name",
            order_dir="asc",
            limit=200,
            offset=0,
        )
        context = _base_context(request, db, active_page="channels")
        context.update(
            {
                "channel_types": [t.value for t in ChannelType],
                "teams": teams,
                "targets": targets,
                "error": str(exc),
                "form": {
                    "team_id": team_id,
                    "channel_type": channel_type,
                    "channel_target_id": channel_target_id or "",
                    "is_active": is_active,
                },
            }
        )
        return templates.TemplateResponse("admin/integrations/channels/new.html", context, status_code=400)
    return RedirectResponse(url=f"/admin/integrations/channels/{channel.id}", status_code=303)


@router.get("/channels/{channel_id}", response_class=HTMLResponse)
def channel_detail(request: Request, channel_id: str, db: Session = Depends(get_db)):
    """Channel detail view."""
    from app.models.crm.enums import ChannelType
    from app.services.crm import team as crm_team_service

    try:
        channel = crm_team_service.TeamChannels.get(db, channel_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        context = _base_context(request, db, active_page="channels")
        context["message"] = "The channel you are looking for does not exist."
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)

    teams = crm_team_service.Teams.list(
        db=db,
        is_active=None,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    targets = integration_service.integration_targets.list(
        db=db,
        target_type=None,
        is_active=None,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )

    context = _base_context(request, db, active_page="channels")
    context.update(
        {
            "channel": channel,
            "teams": teams,
            "targets": targets,
            "channel_types": [t.value for t in ChannelType],
        }
    )
    return templates.TemplateResponse("admin/integrations/channels/detail.html", context)


@router.post("/channels/{channel_id}", response_class=HTMLResponse)
def channel_update(
    request: Request,
    channel_id: str,
    team_id: str = Form(...),
    channel_type: str = Form(...),
    channel_target_id: str | None = Form(None),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
):
    """Update a CRM channel."""
    from pydantic import BaseModel

    from app.models.crm.enums import ChannelType
    from app.services.crm import team as crm_team_service

    class ChannelUpdate(BaseModel):
        team_id: UUID | None = None
        channel_type: ChannelType | None = None
        channel_target_id: UUID | None = None
        is_active: bool | None = None

    try:
        payload = ChannelUpdate(
            team_id=_parse_uuid(team_id, "team_id"),
            channel_type=ChannelType(channel_type),
            channel_target_id=_parse_uuid(channel_target_id, "channel_target_id", required=False),
            is_active=is_active,
        )
        channel = crm_team_service.TeamChannels.update(db, channel_id, payload)
    except Exception as exc:
        channel = crm_team_service.TeamChannels.get(db, channel_id)
        teams = crm_team_service.Teams.list(
            db=db,
            is_active=None,
            order_by="name",
            order_dir="asc",
            limit=200,
            offset=0,
        )
        targets = integration_service.integration_targets.list(
            db=db,
            target_type=None,
            is_active=None,
            order_by="name",
            order_dir="asc",
            limit=200,
            offset=0,
        )
        context = _base_context(request, db, active_page="channels")
        context.update(
            {
                "channel": channel,
                "teams": teams,
                "targets": targets,
                "channel_types": [t.value for t in ChannelType],
                "error": str(exc),
            }
        )
        return templates.TemplateResponse("admin/integrations/channels/detail.html", context, status_code=400)
    return RedirectResponse(url=f"/admin/integrations/channels/{channel.id}", status_code=303)


@router.post("/channels/{channel_id}/delete", response_class=HTMLResponse)
def channel_delete(request: Request, channel_id: str, db: Session = Depends(get_db)):
    """Delete a CRM channel."""
    from app.services.crm import team as crm_team_service

    try:
        crm_team_service.TeamChannels.delete(db, channel_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        context = _base_context(request, db, active_page="channels")
        context["message"] = "The channel you are trying to delete does not exist."
        return templates.TemplateResponse("admin/errors/404.html", context, status_code=404)
    return RedirectResponse(url="/admin/integrations/channels", status_code=303)


@router.post("/channels/{channel_id}/test", response_class=HTMLResponse)
def channel_test(request: Request, channel_id: str, db: Session = Depends(get_db)):
    """Test a CRM channel connection (HTMX)."""
    from app.services.crm import team as crm_team_service

    try:
        channel = crm_team_service.TeamChannels.get(db, channel_id)
    except HTTPException:
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
            "Channel not found."
            "</div>",
            status_code=404,
        )

    # Test based on channel type
    channel_type = channel.channel_type.value if channel.channel_type else "unknown"

    # For now, just return a success message since actual testing depends on channel type
    # In production, this would test the actual integration (e.g., SMTP for email, WhatsApp API, etc.)
    if channel.is_active:
        return HTMLResponse(
            '<div class="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700 dark:border-green-800 dark:bg-green-900/30 dark:text-green-400">'
            f"Channel ({channel_type}) is active and ready."
            "</div>",
            status_code=200,
        )
    else:
        return HTMLResponse(
            '<div class="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-400">'
            f"Channel ({channel_type}) is inactive."
            "</div>",
            status_code=200,
        )


# ==================== ERPNext Import ====================


def _get_erpnext_env_config() -> dict[str, str | None]:
    """Get ERPNext configuration from environment variables."""
    from app.config import settings

    return {
        "url": settings.erpnext_url,
        "api_key": settings.erpnext_api_key,
        "api_secret": settings.erpnext_api_secret,
    }


def _erpnext_env_configured() -> bool:
    """Check if ERPNext is configured via environment variables."""
    config = _get_erpnext_env_config()
    return all([config["url"], config["api_key"], config["api_secret"]])


@router.get("/erpnext", response_class=HTMLResponse)
def erpnext_import_page(request: Request, db: Session = Depends(get_db)):
    """ERPNext one-time import configuration page."""
    # Get ERPNext connector if configured
    from app.models.connector import ConnectorType

    erpnext_connectors = connector_service.connector_configs.list(
        db=db,
        connector_type=ConnectorType.erpnext if hasattr(ConnectorType, "erpnext") else None,
        auth_type=None,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=100,
        offset=0,
    )

    # Also get generic connectors that might be used for ERPNext
    all_connectors = connector_service.connector_configs.list(
        db=db,
        connector_type=None,
        auth_type=None,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=100,
        offset=0,
    )

    # Check if environment variables are configured
    env_config = _get_erpnext_env_config()
    env_configured = _erpnext_env_configured()

    context = _base_context(request, db, active_page="erpnext")
    context.update(
        {
            "connectors": all_connectors,
            "erpnext_connectors": erpnext_connectors,
            "env_configured": env_configured,
            "env_url": env_config["url"],
        }
    )
    return templates.TemplateResponse("admin/integrations/erpnext/index.html", context)


@router.post("/erpnext/test", response_class=HTMLResponse)
def erpnext_test_connection(
    request: Request,
    connector_id: str | None = Form(None),
    base_url: str | None = Form(None),
    api_key: str | None = Form(None),
    api_secret: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Test ERPNext API connection (HTMX)."""
    from app.services.erpnext import ERPNextClient
    from app.services.erpnext.client import ERPNextError

    # Get credentials from environment, connector, or form
    if connector_id == "env":
        # Use environment variables
        env_config = _get_erpnext_env_config()
        base_url = env_config["url"]
        api_key = env_config["api_key"]
        api_secret = env_config["api_secret"]
    elif connector_id:
        try:
            connector = connector_service.connector_configs.get(db, connector_id)
            base_url = connector.base_url
            auth_config = connector.auth_config or {}
            api_key = auth_config.get("api_key")
            api_secret = auth_config.get("api_secret")
        except HTTPException:
            return HTMLResponse(
                '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
                "Connector not found."
                "</div>",
                status_code=404,
            )

    if not base_url or not api_key or not api_secret:
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
            "Missing required credentials: base_url, api_key, api_secret"
            "</div>",
            status_code=400,
        )

    try:
        client = ERPNextClient(base_url, api_key, api_secret)
        client.test_connection()
        return HTMLResponse(
            '<div class="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700 dark:border-green-800 dark:bg-green-900/30 dark:text-green-400">'
            f"Successfully connected to ERPNext at {base_url}"
            "</div>",
            status_code=200,
        )
    except ERPNextError as e:
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
            f"Connection failed: {e.message}"
            "</div>",
            status_code=400,
        )


@router.post("/erpnext/import")
def erpnext_run_import(
    request: Request,
    connector_id: str = Form(...),
    db: Session = Depends(get_db),
):
    """Run one-time ERPNext import.

    Returns JSON with import statistics.
    Use connector_id="env" to use environment variables.
    """
    from app.services.erpnext import ERPNextImporter
    from app.services.erpnext.client import ERPNextError

    # Get credentials from environment or connector
    if connector_id == "env":
        env_config = _get_erpnext_env_config()
        base_url = env_config["url"]
        api_key = env_config["api_key"]
        api_secret = env_config["api_secret"]
        config_id = None  # No connector config when using env vars
    else:
        try:
            connector = connector_service.connector_configs.get(db, connector_id)
        except HTTPException:
            raise HTTPException(status_code=404, detail="Connector not found")

        base_url = connector.base_url
        auth_config = connector.auth_config or {}
        api_key = auth_config.get("api_key")
        api_secret = auth_config.get("api_secret")
        config_id = connector.id

    if not base_url:
        raise HTTPException(status_code=400, detail="Missing base_url configuration")

    if not api_key or not api_secret:
        raise HTTPException(status_code=400, detail="Missing api_key or api_secret configuration")

    try:
        importer = ERPNextImporter(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            connector_config_id=config_id,
        )
        result = importer.import_all(db)
        return result.to_dict()
    except ERPNextError as e:
        raise HTTPException(status_code=502, detail=f"ERPNext API error: {e.message}")


@router.post("/erpnext/import/htmx", response_class=HTMLResponse)
def erpnext_run_import_htmx(
    request: Request,
    connector_id: str = Form(...),
    db: Session = Depends(get_db),
):
    """Run one-time ERPNext import (HTMX response).

    Use connector_id="env" to use environment variables.
    """
    from app.services.erpnext import ERPNextImporter
    from app.services.erpnext.client import ERPNextError

    # Get credentials from environment or connector
    if connector_id == "env":
        env_config = _get_erpnext_env_config()
        base_url = env_config["url"]
        api_key = env_config["api_key"]
        api_secret = env_config["api_secret"]
        config_id = None
    else:
        try:
            connector = connector_service.connector_configs.get(db, connector_id)
        except HTTPException:
            return HTMLResponse(
                '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
                "Connector not found."
                "</div>",
                status_code=404,
            )

        base_url = connector.base_url
        auth_config = connector.auth_config or {}
        api_key = auth_config.get("api_key")
        api_secret = auth_config.get("api_secret")
        config_id = connector.id

    if not base_url:
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
            "Missing base_url configuration."
            "</div>",
            status_code=400,
        )

    if not api_key or not api_secret:
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
            "Missing api_key or api_secret configuration."
            "</div>",
            status_code=400,
        )

    try:
        importer = ERPNextImporter(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret,
            connector_config_id=config_id,
        )
        result = importer.import_all(db)

        # Build summary HTML
        stats = result.to_dict()
        total_created = sum(
            s["created"]
            for s in [
                stats["contacts"],
                stats["customers"],
                stats["tickets"],
                stats["projects"],
                stats["tasks"],
                stats["leads"],
                stats["quotes"],
            ]
        )
        total_updated = sum(
            s["updated"]
            for s in [
                stats["contacts"],
                stats["customers"],
                stats["tickets"],
                stats["projects"],
                stats["tasks"],
                stats["leads"],
                stats["quotes"],
            ]
        )
        total_errors = sum(
            s["errors"]
            for s in [
                stats["contacts"],
                stats["customers"],
                stats["tickets"],
                stats["projects"],
                stats["tasks"],
                stats["leads"],
                stats["quotes"],
            ]
        )

        status_class = "green" if stats["success"] else "red"

        html = f"""
<div class="rounded-lg border border-{status_class}-200 bg-{status_class}-50 p-4 dark:border-{status_class}-800 dark:bg-{status_class}-900/30">
    <h3 class="text-lg font-medium text-{status_class}-800 dark:text-{status_class}-200 mb-3">
        Import {"Completed" if stats["success"] else "Failed"}
    </h3>
    <div class="grid grid-cols-3 gap-4 text-sm">
        <div class="text-center">
            <div class="text-2xl font-bold text-green-600 dark:text-green-400">{total_created}</div>
            <div class="text-gray-600 dark:text-gray-400">Created</div>
        </div>
        <div class="text-center">
            <div class="text-2xl font-bold text-blue-600 dark:text-blue-400">{total_updated}</div>
            <div class="text-gray-600 dark:text-gray-400">Updated</div>
        </div>
        <div class="text-center">
            <div class="text-2xl font-bold text-red-600 dark:text-red-400">{total_errors}</div>
            <div class="text-gray-600 dark:text-gray-400">Errors</div>
        </div>
    </div>
    <div class="mt-4 text-xs text-gray-600 dark:text-gray-400">
        <table class="w-full">
            <thead>
                <tr class="border-b border-gray-200 dark:border-gray-700">
                    <th class="py-1 text-left">Entity</th>
                    <th class="py-1 text-right">Created</th>
                    <th class="py-1 text-right">Updated</th>
                    <th class="py-1 text-right">Skipped</th>
                    <th class="py-1 text-right">Errors</th>
                </tr>
            </thead>
            <tbody>
                <tr><td>Contacts</td><td class="text-right">{stats["contacts"]["created"]}</td><td class="text-right">{stats["contacts"]["updated"]}</td><td class="text-right">{stats["contacts"]["skipped"]}</td><td class="text-right">{stats["contacts"]["errors"]}</td></tr>
                <tr><td>Customers</td><td class="text-right">{stats["customers"]["created"]}</td><td class="text-right">{stats["customers"]["updated"]}</td><td class="text-right">{stats["customers"]["skipped"]}</td><td class="text-right">{stats["customers"]["errors"]}</td></tr>
                <tr><td>Projects</td><td class="text-right">{stats["projects"]["created"]}</td><td class="text-right">{stats["projects"]["updated"]}</td><td class="text-right">{stats["projects"]["skipped"]}</td><td class="text-right">{stats["projects"]["errors"]}</td></tr>
                <tr><td>Tasks</td><td class="text-right">{stats["tasks"]["created"]}</td><td class="text-right">{stats["tasks"]["updated"]}</td><td class="text-right">{stats["tasks"]["skipped"]}</td><td class="text-right">{stats["tasks"]["errors"]}</td></tr>
                <tr><td>Tickets</td><td class="text-right">{stats["tickets"]["created"]}</td><td class="text-right">{stats["tickets"]["updated"]}</td><td class="text-right">{stats["tickets"]["skipped"]}</td><td class="text-right">{stats["tickets"]["errors"]}</td></tr>
                <tr><td>Leads</td><td class="text-right">{stats["leads"]["created"]}</td><td class="text-right">{stats["leads"]["updated"]}</td><td class="text-right">{stats["leads"]["skipped"]}</td><td class="text-right">{stats["leads"]["errors"]}</td></tr>
                <tr><td>Quotes</td><td class="text-right">{stats["quotes"]["created"]}</td><td class="text-right">{stats["quotes"]["updated"]}</td><td class="text-right">{stats["quotes"]["skipped"]}</td><td class="text-right">{stats["quotes"]["errors"]}</td></tr>
            </tbody>
        </table>
    </div>
</div>
"""
        return HTMLResponse(html, status_code=200)

    except ERPNextError as e:
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
            f"ERPNext API error: {e.message}"
            "</div>",
            status_code=502,
        )


# ==================== DotMac ERP Sync ====================


def _humanize_time_ago(dt_str: str | None) -> str:
    """Convert ISO datetime string to human-readable time ago."""
    if not dt_str:
        return "Never"
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        now = datetime.now(UTC)
        diff = now - dt
        seconds = diff.total_seconds()

        if seconds < 60:
            return "Just now"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            return f"{minutes}m ago"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f"{hours}h ago"
        else:
            days = int(seconds / 86400)
            return f"{days}d ago"
    except Exception:
        return "Unknown"


@router.get("/dotmac-erp", response_class=HTMLResponse)
def dotmac_erp_index(
    request: Request,
    saved: str | None = Query(None),
    error: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """DotMac ERP sync dashboard page."""
    from app.models.domain_settings import SettingDomain
    from app.services import settings_spec
    from app.services.dotmac_erp import (
        get_contact_sync_history,
        get_daily_stats,
        get_inventory_sync_history,
        get_last_contact_sync,
        get_last_inventory_sync,
        get_last_sync,
        get_sync_history,
    )

    # Get configuration
    enabled = settings_spec.resolve_value(db, SettingDomain.integration, "dotmac_erp_sync_enabled")
    base_url = settings_spec.resolve_value(db, SettingDomain.integration, "dotmac_erp_base_url")
    token = settings_spec.resolve_value(db, SettingDomain.integration, "dotmac_erp_token")
    timeout = settings_spec.resolve_value(db, SettingDomain.integration, "dotmac_erp_timeout_seconds") or 30
    interval = settings_spec.resolve_value(db, SettingDomain.integration, "dotmac_erp_sync_interval_minutes") or 60

    # Get outbound sync stats (push to ERP)
    daily_stats = get_daily_stats()
    last_sync = get_last_sync()
    history = get_sync_history(limit=10)

    # Get inventory sync stats (pull from ERP)
    last_inventory_sync = get_last_inventory_sync()
    inventory_history = get_inventory_sync_history(limit=10)
    last_contact_sync = get_last_contact_sync()
    contact_history = get_contact_sync_history(limit=10)

    # Format last sync times
    last_sync_ago = _humanize_time_ago(last_sync.get("timestamp") if last_sync else None)
    last_inventory_sync_ago = _humanize_time_ago(last_inventory_sync.get("timestamp") if last_inventory_sync else None)
    last_contact_sync_ago = _humanize_time_ago(last_contact_sync.get("timestamp") if last_contact_sync else None)

    # Calculate total today
    total_today = daily_stats.get("projects", 0) + daily_stats.get("tickets", 0) + daily_stats.get("work_orders", 0)

    context = _base_context(request, db, active_page="dotmac-erp")
    context.update(
        {
            "enabled": bool(enabled),
            "base_url": base_url or "",
            "has_token": bool(token),
            "timeout": timeout,
            "interval": interval,
            "daily_stats": daily_stats,
            "total_today": total_today,
            "last_sync": last_sync,
            "last_sync_ago": last_sync_ago,
            "history": history,
            "last_inventory_sync": last_inventory_sync,
            "last_inventory_sync_ago": last_inventory_sync_ago,
            "inventory_history": inventory_history,
            "last_contact_sync": last_contact_sync,
            "last_contact_sync_ago": last_contact_sync_ago,
            "contact_history": contact_history,
            "humanize_time_ago": _humanize_time_ago,
            "settings_saved": bool(saved),
            "settings_error": error,
        }
    )
    return templates.TemplateResponse("admin/integrations/dotmac_erp.html", context)


@router.post("/dotmac-erp/settings")
def dotmac_erp_save_settings(
    request: Request,
    enabled: str | None = Form(None),
    base_url: str | None = Form(None),
    token: str | None = Form(None),
    timeout: str | None = Form(None),
    interval: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Save DotMac ERP sync settings."""
    from app.models.domain_settings import DomainSetting, SettingDomain, SettingValueType

    errors = []

    # Validate inputs
    timeout_val = 30
    if timeout:
        try:
            timeout_val = int(timeout)
            if timeout_val < 5 or timeout_val > 120:
                errors.append("Timeout must be between 5 and 120 seconds")
        except ValueError:
            errors.append("Timeout must be a number")

    interval_val = 60
    if interval:
        try:
            interval_val = int(interval)
            if interval_val < 5:
                errors.append("Sync interval must be at least 5 minutes")
        except ValueError:
            errors.append("Interval must be a number")

    if errors:
        return RedirectResponse(
            url=f"/admin/integrations/dotmac-erp?error={errors[0]}",
            status_code=303,
        )

    def _format_value_text(raw_value, value_type: SettingValueType) -> str:
        if value_type == SettingValueType.boolean:
            return "true" if bool(raw_value) else "false"
        if value_type == SettingValueType.integer:
            return str(int(raw_value))
        return str(raw_value) if raw_value is not None else ""

    def _upsert_setting(key: str, value, value_type: SettingValueType):
        value_text = _format_value_text(value, value_type)
        setting = (
            db.query(DomainSetting)
            .filter(DomainSetting.domain == SettingDomain.integration)
            .filter(DomainSetting.key == key)
            .first()
        )
        if setting:
            setting.value_text = value_text
            setting.value_json = None
            setting.is_active = True
        else:
            setting = DomainSetting(
                domain=SettingDomain.integration,
                key=key,
                value_type=value_type,
                is_active=True,
                value_text=value_text,
            )
            db.add(setting)

    # Save enabled
    _upsert_setting("dotmac_erp_sync_enabled", bool(enabled), SettingValueType.boolean)

    # Save base_url
    if base_url and base_url.strip():
        _upsert_setting("dotmac_erp_base_url", base_url.strip(), SettingValueType.string)

    # Save token (only if provided - keep existing otherwise)
    if token and token.strip():
        _upsert_setting("dotmac_erp_token", token.strip(), SettingValueType.string)

    # Save timeout
    _upsert_setting("dotmac_erp_timeout_seconds", timeout_val, SettingValueType.integer)

    # Save interval
    _upsert_setting("dotmac_erp_sync_interval_minutes", interval_val, SettingValueType.integer)

    db.commit()

    return RedirectResponse(
        url="/admin/integrations/dotmac-erp?saved=1",
        status_code=303,
    )


@router.post("/dotmac-erp/test", response_class=HTMLResponse)
def dotmac_erp_test(request: Request, db: Session = Depends(get_db)):
    """Test DotMac ERP connection (HTMX)."""
    from app.services.dotmac_erp import DotMacERPSync

    try:
        sync_service = DotMacERPSync(db)
        client = sync_service._get_client()

        if not client:
            return HTMLResponse(
                '<div class="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-400">'
                "ERP sync is not configured. Please set the base URL and token in Settings."
                "</div>",
                status_code=200,
            )

        success = client.test_connection()
        sync_service.close()

        if success:
            return HTMLResponse(
                '<div class="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700 dark:border-green-800 dark:bg-green-900/30 dark:text-green-400">'
                "Connection successful! API is reachable and authenticated."
                "</div>",
                status_code=200,
            )
        else:
            return HTMLResponse(
                '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
                "Connection failed. Please check the base URL and token."
                "</div>",
                status_code=200,
            )

    except Exception as e:
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
            f"Connection error: {e!s}"
            "</div>",
            status_code=200,
        )


@router.post("/dotmac-erp/sync", response_class=HTMLResponse)
def dotmac_erp_sync_now(
    request: Request,
    mode: str = Form("recently_updated"),
    db: Session = Depends(get_db),
):
    """Trigger manual DotMac ERP sync (HTMX)."""
    from app.tasks.integrations import sync_dotmac_erp

    valid_modes = ["recently_updated", "all_active"]
    if mode not in valid_modes:
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
            f"Invalid mode. Must be one of: {', '.join(valid_modes)}"
            "</div>",
            status_code=400,
        )

    try:
        # Queue Celery task
        task = sync_dotmac_erp.delay(mode=mode)

        mode_label = "recently updated" if mode == "recently_updated" else "all active"
        return HTMLResponse(
            '<div class="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-700 dark:border-blue-800 dark:bg-blue-900/30 dark:text-blue-400">'
            f"Sync started for {mode_label} entities. Task ID: {task.id[:8]}..."
            '<br><span class="text-xs">Refresh the page to see results.</span>'
            "</div>",
            status_code=200,
        )

    except Exception as e:
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
            f"Failed to queue sync task: {e!s}"
            "</div>",
            status_code=500,
        )


@router.post("/dotmac-erp/inventory-sync", response_class=HTMLResponse)
def dotmac_erp_inventory_sync_now(request: Request, db: Session = Depends(get_db)):
    """Trigger manual inventory sync from DotMac ERP (HTMX)."""
    from app.tasks.integrations import sync_dotmac_erp_inventory

    try:
        # Queue Celery task
        task = sync_dotmac_erp_inventory.delay()

        return HTMLResponse(
            '<div class="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-700 dark:border-blue-800 dark:bg-blue-900/30 dark:text-blue-400">'
            f"Inventory sync started. Task ID: {task.id[:8]}..."
            '<br><span class="text-xs">Refresh the page to see results.</span>'
            "</div>",
            status_code=200,
        )

    except Exception as e:
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
            f"Failed to queue inventory sync task: {e!s}"
            "</div>",
            status_code=500,
        )


@router.post("/dotmac-erp/contacts-sync", response_class=HTMLResponse)
def dotmac_erp_contacts_sync_now(request: Request, db: Session = Depends(get_db)):
    """Trigger manual contacts sync from DotMac ERP (HTMX)."""
    from app.tasks.integrations import sync_dotmac_erp_contacts

    try:
        task = sync_dotmac_erp_contacts.delay()

        return HTMLResponse(
            '<div class="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-700 dark:border-blue-800 dark:bg-blue-900/30 dark:text-blue-400">'
            f"Contacts sync started. Task ID: {task.id[:8]}..."
            '<br><span class="text-xs">Refresh the page to see results.</span>'
            "</div>",
            status_code=200,
        )

    except Exception as e:
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
            f"Failed to queue contacts sync task: {html_escape(str(e))}"
            "</div>",
            status_code=500,
        )
