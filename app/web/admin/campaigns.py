"""Admin campaign management web routes."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.enums import CampaignChannel, CampaignType
from app.models.crm.sales import Pipeline, PipelineStage
from app.models.person import PartyStatus, Person
from app.schemas.crm.campaign import (
    CampaignStepCreate,
    CampaignStepUpdate,
)
from app.services.crm.campaign_permissions import can_view_campaigns, can_write_campaigns
from app.services.crm.campaigns import (
    Campaigns,
)
from app.services.crm.campaigns import (
    campaign_recipients as recipients_service,
)
from app.services.crm.campaigns import (
    campaign_steps as steps_service,
)
from app.services.crm.campaigns import (
    campaigns as campaigns_service,
)
from app.services.crm.web_campaigns import (
    CampaignUpsertInput,
    build_campaign_create_payload,
    build_campaign_form_stub,
    build_campaign_update_payload,
    campaign_detail_page_data,
    resolve_campaign_upsert,
)
from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats
from app.web.admin.crm import REGION_OPTIONS

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/crm/campaigns", tags=["web-admin-campaigns"])


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _form_str(value: object | None) -> str:
    return value if isinstance(value, str) else ""


def _form_str_opt(value: object | None) -> str | None:
    value_str = _form_str(value).strip()
    return value_str or None


def _parse_datetime_opt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _load_campaign_senders(db: Session):
    from app.services.crm.campaign_senders import campaign_senders

    return campaign_senders.list(
        db=db,
        is_active=None,
        order_by="name",
        order_dir="asc",
        limit=500,
        offset=0,
    )


def _load_campaign_smtp_profiles(db: Session):
    from app.services.crm.campaign_smtp_configs import campaign_smtp_configs

    return campaign_smtp_configs.list(
        db=db,
        is_active=None,
        order_by="name",
        order_dir="asc",
        limit=500,
        offset=0,
    )


def _load_whatsapp_connectors(db: Session):
    return (
        db.query(ConnectorConfig)
        .filter(ConnectorConfig.connector_type == ConnectorType.whatsapp)
        .order_by(ConnectorConfig.name.asc())
        .limit(500)
        .all()
    )


def _load_active_pipelines(db: Session):
    return db.query(Pipeline).filter(Pipeline.is_active.is_(True)).order_by(Pipeline.name.asc()).limit(200).all()


def _load_active_pipeline_stages(db: Session):
    return (
        db.query(PipelineStage)
        .join(Pipeline, PipelineStage.pipeline_id == Pipeline.id)
        .filter(PipelineStage.is_active.is_(True))
        .filter(Pipeline.is_active.is_(True))
        .order_by(Pipeline.name.asc(), PipelineStage.order_index.asc(), PipelineStage.name.asc())
        .limit(500)
        .all()
    )


def _base_ctx(request: Request, db: Session, **kwargs) -> dict:
    current_user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)
    return {
        "request": request,
        "current_user": current_user,
        "sidebar_stats": sidebar_stats,
        "active_page": "campaigns",
        **kwargs,
    }


def _get_current_roles(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        roles = auth.get("roles") or []
        if isinstance(roles, list):
            return [str(role) for role in roles]
    return []


def _get_current_scopes(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        scopes = auth.get("scopes") or []
        if isinstance(scopes, list):
            return [str(scope) for scope in scopes]
    return []


def _forbidden_html() -> HTMLResponse:
    return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("", response_class=HTMLResponse)
def campaign_list(
    request: Request,
    db: Session = Depends(_get_db),
    status: str | None = Query(None),
    search: str | None = Query(None),
    order_by: str = Query("created_at"),
    order_dir: str = Query("desc"),
):
    if not can_view_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    if order_by not in {"created_at", "updated_at", "name"}:
        order_by = "created_at"
    if order_dir not in {"asc", "desc"}:
        order_dir = "desc"
    items = campaigns_service.list(db, status=status, search=search, order_by=order_by, order_dir=order_dir)
    status_counts = Campaigns.count_by_status(db)
    ctx = _base_ctx(
        request,
        db,
        campaigns=items,
        status_counts=status_counts,
        filter_status=status or "",
        search=search or "",
        order_by=order_by,
        order_dir=order_dir,
    )
    return templates.TemplateResponse("admin/crm/campaigns.html", ctx)


# ── Create ────────────────────────────────────────────────────────────────────


@router.get("/new", response_class=HTMLResponse)
def campaign_create_form(request: Request, db: Session = Depends(_get_db)):
    if not can_write_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    ctx = _base_ctx(
        request,
        db,
        campaign=None,
        campaign_types=CampaignType,
        campaign_channels=CampaignChannel,
        party_statuses=PartyStatus,
        region_options=REGION_OPTIONS,
        pipelines=_load_active_pipelines(db),
        pipeline_stages=_load_active_pipeline_stages(db),
        campaign_senders=_load_campaign_senders(db),
        campaign_smtp_profiles=_load_campaign_smtp_profiles(db),
        whatsapp_connectors=_load_whatsapp_connectors(db),
        errors=[],
    )
    return templates.TemplateResponse("admin/crm/campaign_form.html", ctx)


@router.post("", response_class=HTMLResponse)
def campaign_create(
    request: Request,
    db: Session = Depends(_get_db),
    name: str = Form(...),
    campaign_type: str = Form("one_time"),
    channel: str = Form("email"),
    subject: str = Form(""),
    body_html: str = Form(""),
    body_text: str = Form(""),
    campaign_sender_id: str = Form(""),
    campaign_smtp_config_id: str = Form(""),
    whatsapp_connector_id: str = Form(""),
    seg_party_status: list[str] = Form([]),
    seg_regions: list[str] = Form([]),
    seg_pipeline_ids: list[str] = Form([]),
    seg_stage_ids: list[str] = Form([]),
    seg_active_status: str = Form("active"),
    seg_created_after: str = Form(""),
    seg_created_before: str = Form(""),
    whatsapp_template_name: str = Form(""),
    whatsapp_template_language: str = Form(""),
    whatsapp_template_components: str = Form(""),
):
    if not can_write_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    current_user = get_current_user(request)
    created_by_id = current_user.get("person_id") if current_user else None
    resolved = resolve_campaign_upsert(
        db,
        form=CampaignUpsertInput(
            campaign_type=campaign_type,
            channel=channel,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            campaign_sender_id=campaign_sender_id,
            campaign_smtp_config_id=campaign_smtp_config_id,
            whatsapp_connector_id=whatsapp_connector_id,
            seg_party_status=seg_party_status,
            seg_regions=seg_regions,
            seg_pipeline_ids=seg_pipeline_ids,
            seg_stage_ids=seg_stage_ids,
            seg_active_status=seg_active_status,
            seg_created_after=seg_created_after,
            seg_created_before=seg_created_before,
            whatsapp_template_name=whatsapp_template_name,
            whatsapp_template_language=whatsapp_template_language,
            whatsapp_template_components=whatsapp_template_components,
        ),
    )
    if resolved.errors:
        campaign_stub = build_campaign_form_stub(
            campaign_id=None,
            name=name,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            resolved=resolved,
        )
        ctx = _base_ctx(
            request,
            db,
            campaign=campaign_stub,
            campaign_types=CampaignType,
            campaign_channels=CampaignChannel,
            party_statuses=PartyStatus,
            region_options=REGION_OPTIONS,
            pipelines=_load_active_pipelines(db),
            pipeline_stages=_load_active_pipeline_stages(db),
            campaign_senders=_load_campaign_senders(db),
            campaign_smtp_profiles=_load_campaign_smtp_profiles(db),
            whatsapp_connectors=_load_whatsapp_connectors(db),
            errors=resolved.errors,
        )
        return templates.TemplateResponse("admin/crm/campaign_form.html", ctx, status_code=400)
    payload = build_campaign_create_payload(
        name=name,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        resolved=resolved,
    )
    campaign = campaigns_service.create(db, payload, created_by_id=created_by_id)
    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign.id}", status_code=303)


# ── Detail ────────────────────────────────────────────────────────────────────


@router.get("/{campaign_id}", response_class=HTMLResponse)
def campaign_detail(
    request: Request,
    campaign_id: str,
    db: Session = Depends(_get_db),
):
    if not can_view_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    ctx = _base_ctx(request, db, **campaign_detail_page_data(db, campaign_id=campaign_id))
    return templates.TemplateResponse("admin/crm/campaign_detail.html", ctx)


# ── Edit ──────────────────────────────────────────────────────────────────────


@router.get("/{campaign_id}/edit", response_class=HTMLResponse)
def campaign_edit_form(
    request: Request,
    campaign_id: str,
    db: Session = Depends(_get_db),
):
    if not can_write_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    campaign = campaigns_service.get(db, campaign_id)
    ctx = _base_ctx(
        request,
        db,
        campaign=campaign,
        campaign_types=CampaignType,
        campaign_channels=CampaignChannel,
        party_statuses=PartyStatus,
        region_options=REGION_OPTIONS,
        pipelines=_load_active_pipelines(db),
        pipeline_stages=_load_active_pipeline_stages(db),
        campaign_senders=_load_campaign_senders(db),
        campaign_smtp_profiles=_load_campaign_smtp_profiles(db),
        whatsapp_connectors=_load_whatsapp_connectors(db),
        errors=[],
    )
    return templates.TemplateResponse("admin/crm/campaign_form.html", ctx)


@router.post("/{campaign_id}", response_class=HTMLResponse)
def campaign_update(
    request: Request,
    campaign_id: str,
    db: Session = Depends(_get_db),
    name: str = Form(...),
    campaign_type: str = Form("one_time"),
    channel: str = Form("email"),
    subject: str = Form(""),
    body_html: str = Form(""),
    body_text: str = Form(""),
    campaign_sender_id: str = Form(""),
    campaign_smtp_config_id: str = Form(""),
    whatsapp_connector_id: str = Form(""),
    seg_party_status: list[str] = Form([]),
    seg_regions: list[str] = Form([]),
    seg_pipeline_ids: list[str] = Form([]),
    seg_stage_ids: list[str] = Form([]),
    seg_active_status: str = Form("active"),
    seg_created_after: str = Form(""),
    seg_created_before: str = Form(""),
    whatsapp_template_name: str = Form(""),
    whatsapp_template_language: str = Form(""),
    whatsapp_template_components: str = Form(""),
):
    if not can_write_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    resolved = resolve_campaign_upsert(
        db,
        form=CampaignUpsertInput(
            campaign_type=campaign_type,
            channel=channel,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            campaign_sender_id=campaign_sender_id,
            campaign_smtp_config_id=campaign_smtp_config_id,
            whatsapp_connector_id=whatsapp_connector_id,
            seg_party_status=seg_party_status,
            seg_regions=seg_regions,
            seg_pipeline_ids=seg_pipeline_ids,
            seg_stage_ids=seg_stage_ids,
            seg_active_status=seg_active_status,
            seg_created_after=seg_created_after,
            seg_created_before=seg_created_before,
            whatsapp_template_name=whatsapp_template_name,
            whatsapp_template_language=whatsapp_template_language,
            whatsapp_template_components=whatsapp_template_components,
        ),
    )
    if resolved.errors:
        campaign_stub = build_campaign_form_stub(
            campaign_id=campaign_id,
            name=name,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            resolved=resolved,
        )
        ctx = _base_ctx(
            request,
            db,
            campaign=campaign_stub,
            campaign_types=CampaignType,
            campaign_channels=CampaignChannel,
            party_statuses=PartyStatus,
            region_options=REGION_OPTIONS,
            pipelines=_load_active_pipelines(db),
            pipeline_stages=_load_active_pipeline_stages(db),
            campaign_senders=_load_campaign_senders(db),
            campaign_smtp_profiles=_load_campaign_smtp_profiles(db),
            whatsapp_connectors=_load_whatsapp_connectors(db),
            errors=resolved.errors,
        )
        return templates.TemplateResponse("admin/crm/campaign_form.html", ctx, status_code=400)
    payload = build_campaign_update_payload(
        name=name,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        resolved=resolved,
    )
    campaigns_service.update(db, campaign_id, payload)
    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)


# ── Delete ────────────────────────────────────────────────────────────────────


@router.post("/{campaign_id}/delete")
def campaign_delete(
    request: Request,
    campaign_id: str,
    db: Session = Depends(_get_db),
):
    if not can_write_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    campaigns_service.delete(db, campaign_id)
    return RedirectResponse(url="/admin/crm/campaigns", status_code=303)


# ── Actions ───────────────────────────────────────────────────────────────────


@router.post("/{campaign_id}/schedule")
def campaign_schedule(
    request: Request,
    campaign_id: str,
    db: Session = Depends(_get_db),
    scheduled_at: str = Form(...),
):
    if not can_write_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    dt = _parse_datetime_opt(scheduled_at)
    if not dt:
        return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)
    campaigns_service.schedule(db, campaign_id, dt)
    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)


@router.post("/{campaign_id}/send")
def campaign_send_now(
    request: Request,
    campaign_id: str,
    db: Session = Depends(_get_db),
):
    if not can_write_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    campaigns_service.send_now(db, campaign_id)
    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)


@router.post("/{campaign_id}/cancel")
def campaign_cancel(
    request: Request,
    campaign_id: str,
    db: Session = Depends(_get_db),
):
    if not can_write_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    campaigns_service.cancel(db, campaign_id)
    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)


# ── Preview ───────────────────────────────────────────────────────────────────


@router.get("/{campaign_id}/preview", response_class=HTMLResponse)
def campaign_preview(
    request: Request,
    campaign_id: str,
    db: Session = Depends(_get_db),
):
    if not can_view_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    campaign = campaigns_service.get(db, campaign_id)
    ctx = _base_ctx(request, db, campaign=campaign)
    return templates.TemplateResponse("admin/crm/campaign_preview.html", ctx)


# ── HTMX Partials ────────────────────────────────────────────────────────────


@router.get("/{campaign_id}/preview-audience", response_class=HTMLResponse)
def campaign_preview_audience(
    request: Request,
    campaign_id: str,
    db: Session = Depends(_get_db),
):
    if not can_view_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    campaign = campaigns_service.get(db, campaign_id)
    result = Campaigns.preview_audience(db, campaign.segment_filter, campaign.channel)
    ctx = _base_ctx(
        request,
        db,
        audience=result,
        campaign=campaign,
        audience_address_label="Phone" if campaign.channel == CampaignChannel.whatsapp else "Email",
    )
    return templates.TemplateResponse("admin/crm/_campaign_audience_preview.html", ctx)


@router.get("/templates/whatsapp", response_class=JSONResponse)
def campaign_whatsapp_templates(
    request: Request,
    connector_id: str | None = Query(None),
    db: Session = Depends(_get_db),
):
    from app.services.crm.inbox.whatsapp_templates import list_whatsapp_templates

    if not can_write_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return JSONResponse({"templates": [], "error": "Forbidden"}, status_code=403)

    if not connector_id:
        return JSONResponse({"templates": [], "error": "Connector is required"}, status_code=400)

    try:
        templates_payload = list_whatsapp_templates(db, connector_config_id=connector_id)
    except HTTPException as exc:
        return JSONResponse({"templates": [], "error": str(exc.detail)}, status_code=400)

    approved = [t for t in templates_payload if str(t.get("status", "")).lower() == "approved"]
    return JSONResponse({"templates": approved})


@router.get("/{campaign_id}/recipients", response_class=HTMLResponse)
def campaign_recipients_table(
    request: Request,
    campaign_id: str,
    db: Session = Depends(_get_db),
    status: str | None = Query(None),
    offset: int = Query(0),
):
    if not can_view_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    recipients = recipients_service.list(db, campaign_id, status=status, limit=50, offset=offset)
    person_ids = [r.person_id for r in recipients]
    persons = db.query(Person).filter(Person.id.in_(person_ids)).all() if person_ids else []
    person_map = {str(p.id): p for p in persons}
    ctx = _base_ctx(
        request,
        db,
        recipients=recipients,
        person_map=person_map,
        campaign_id=campaign_id,
    )
    return templates.TemplateResponse("admin/crm/_campaign_recipients_table.html", ctx)


# ── Nurture Steps ────────────────────────────────────────────────────────────


@router.get("/{campaign_id}/steps", response_class=HTMLResponse)
def campaign_steps_list(
    request: Request,
    campaign_id: str,
    db: Session = Depends(_get_db),
):
    if not can_view_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    campaign = campaigns_service.get(db, campaign_id)
    steps = steps_service.list(db, campaign_id)
    ctx = _base_ctx(request, db, campaign=campaign, steps=steps)
    return templates.TemplateResponse("admin/crm/_campaign_steps.html", ctx)


@router.post("/{campaign_id}/steps")
def campaign_step_create(
    request: Request,
    campaign_id: str,
    db: Session = Depends(_get_db),
    name: str = Form(""),
    subject: str = Form(""),
    body_html: str = Form(""),
    body_text: str = Form(""),
    delay_days: int = Form(0),
    step_index: int = Form(0),
):
    if not can_write_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    payload = CampaignStepCreate(
        campaign_id=UUID(campaign_id),
        step_index=step_index,
        name=_form_str_opt(name),
        subject=_form_str_opt(subject),
        body_html=_form_str_opt(body_html),
        body_text=_form_str_opt(body_text),
        delay_days=delay_days,
    )
    steps_service.create(db, payload)
    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)


@router.post("/{campaign_id}/steps/{step_id}")
def campaign_step_update(
    request: Request,
    campaign_id: str,
    step_id: str,
    db: Session = Depends(_get_db),
    name: str = Form(""),
    subject: str = Form(""),
    body_html: str = Form(""),
    body_text: str = Form(""),
    delay_days: int = Form(0),
    step_index: int = Form(0),
):
    if not can_write_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    payload = CampaignStepUpdate(
        step_index=step_index,
        name=_form_str_opt(name),
        subject=_form_str_opt(subject),
        body_html=_form_str_opt(body_html),
        body_text=_form_str_opt(body_text),
        delay_days=delay_days,
    )
    steps_service.update(db, step_id, payload)
    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)


@router.post("/{campaign_id}/steps/{step_id}/delete")
def campaign_step_delete(
    request: Request,
    campaign_id: str,
    step_id: str,
    db: Session = Depends(_get_db),
):
    if not can_write_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    steps_service.delete(db, step_id)
    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)
