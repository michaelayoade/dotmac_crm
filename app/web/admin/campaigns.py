"""Admin campaign management web routes."""

import json
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.enums import CampaignChannel, CampaignType
from app.models.person import PartyStatus, Person
from app.schemas.crm.campaign import (
    CampaignCreate,
    CampaignStepCreate,
    CampaignStepUpdate,
    CampaignUpdate,
)
from app.services.crm.campaign_senders import campaign_senders
from app.services.crm.campaign_smtp_configs import campaign_smtp_configs
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


def _build_segment_filter(
    party_statuses: list[str],
    regions: list[str],
    active_status: str | None,
    created_after: str | None,
    created_before: str | None,
) -> dict | None:
    """Build segment_filter dict from form fields, returning None if empty."""
    sf: dict = {}
    if party_statuses:
        sf["party_status"] = [s for s in party_statuses if s]
    if regions:
        sf["regions"] = [r for r in regions if r]
    if active_status and active_status.strip():
        sf["active_status"] = active_status.strip()
    if created_after and created_after.strip():
        sf["created_after"] = created_after.strip()
    if created_before and created_before.strip():
        sf["created_before"] = created_before.strip()
    return sf or None


def _load_campaign_senders(db: Session):
    return campaign_senders.list(
        db=db,
        is_active=None,
        order_by="name",
        order_dir="asc",
        limit=500,
        offset=0,
    )


def _load_campaign_smtp_profiles(db: Session):
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


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("", response_class=HTMLResponse)
def campaign_list(
    request: Request,
    db: Session = Depends(_get_db),
    status: str | None = Query(None),
    search: str | None = Query(None),
):
    items = campaigns_service.list(db, status=status, search=search)
    status_counts = Campaigns.count_by_status(db)
    ctx = _base_ctx(
        request,
        db,
        campaigns=items,
        status_counts=status_counts,
        filter_status=status or "",
        search=search or "",
    )
    return templates.TemplateResponse("admin/crm/campaigns.html", ctx)


# ── Create ────────────────────────────────────────────────────────────────────


@router.get("/new", response_class=HTMLResponse)
def campaign_create_form(request: Request, db: Session = Depends(_get_db)):
    ctx = _base_ctx(
        request,
        db,
        campaign=None,
        campaign_types=CampaignType,
        campaign_channels=CampaignChannel,
        party_statuses=PartyStatus,
        region_options=REGION_OPTIONS,
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
    seg_active_status: str = Form("active"),
    seg_created_after: str = Form(""),
    seg_created_before: str = Form(""),
    whatsapp_template_name: str = Form(""),
    whatsapp_template_language: str = Form(""),
    whatsapp_template_components: str = Form(""),
):
    current_user = get_current_user(request)
    created_by_id = current_user.get("person_id") if current_user else None

    try:
        ct = CampaignType(campaign_type)
    except ValueError:
        ct = CampaignType.one_time
    try:
        selected_channel = CampaignChannel(channel)
    except ValueError:
        selected_channel = CampaignChannel.email

    segment_filter = _build_segment_filter(
        seg_party_status,
        seg_regions,
        seg_active_status,
        seg_created_after,
        seg_created_before,
    )

    # Parse WhatsApp template components JSON
    wa_template_name = _form_str_opt(whatsapp_template_name)
    wa_template_lang = _form_str_opt(whatsapp_template_language)
    wa_template_components: dict | None = None
    if whatsapp_template_components.strip():
        try:
            wa_template_components = json.loads(whatsapp_template_components)
        except (json.JSONDecodeError, TypeError):
            wa_template_components = None

    errors: list[str] = []
    sender = None
    sender_id_value = campaign_sender_id.strip()
    smtp_profile = None
    smtp_id_value = campaign_smtp_config_id.strip()
    whatsapp_connector = None
    whatsapp_connector_id_value = whatsapp_connector_id.strip()

    if selected_channel == CampaignChannel.email:
        if sender_id_value:
            try:
                sender = campaign_senders.get(db, sender_id_value)
            except HTTPException as exc:
                errors.append(str(exc.detail))
        else:
            errors.append("Campaign sender is required.")

        if sender and not sender.is_active:
            errors.append("Selected campaign sender is inactive.")

        if smtp_id_value:
            try:
                smtp_profile = campaign_smtp_configs.get(db, smtp_id_value)
            except HTTPException as exc:
                errors.append(str(exc.detail))
        else:
            errors.append("Campaign SMTP profile is required.")

        if smtp_profile and not smtp_profile.is_active:
            errors.append("Selected campaign SMTP profile is inactive.")
    else:
        if whatsapp_connector_id_value:
            try:
                whatsapp_connector = db.get(ConnectorConfig, UUID(whatsapp_connector_id_value))
            except ValueError:
                errors.append("Invalid WhatsApp connector.")
                whatsapp_connector = None
            if not whatsapp_connector and "Invalid WhatsApp connector." not in errors:
                errors.append("WhatsApp connector not found.")
            elif whatsapp_connector and whatsapp_connector.connector_type != ConnectorType.whatsapp:
                errors.append("Selected connector is not a WhatsApp connector.")
            elif whatsapp_connector and not whatsapp_connector.is_active:
                errors.append("Selected WhatsApp connector is inactive.")
        else:
            errors.append("WhatsApp connector is required.")

    if errors:
        campaign_stub = SimpleNamespace(
            id=None,
            name=name,
            campaign_type=ct,
            channel=selected_channel,
            subject=subject or None,
            body_html=body_html or None,
            body_text=body_text or None,
            from_name=None,
            from_email=None,
            reply_to=None,
            segment_filter=segment_filter,
            campaign_sender_id=sender_id_value or None,
            campaign_smtp_config_id=smtp_id_value or None,
            connector_config_id=whatsapp_connector_id_value or None,
            whatsapp_template_name=wa_template_name,
            whatsapp_template_language=wa_template_lang,
            whatsapp_template_components=wa_template_components,
        )
        ctx = _base_ctx(
            request,
            db,
            campaign=campaign_stub,
            campaign_types=CampaignType,
            campaign_channels=CampaignChannel,
            party_statuses=PartyStatus,
            region_options=REGION_OPTIONS,
            campaign_senders=_load_campaign_senders(db),
            campaign_smtp_profiles=_load_campaign_smtp_profiles(db),
            whatsapp_connectors=_load_whatsapp_connectors(db),
            errors=errors,
        )
        return templates.TemplateResponse("admin/crm/campaign_form.html", ctx, status_code=400)

    payload = CampaignCreate(
        name=name,
        campaign_type=ct,
        channel=selected_channel,
        subject=_form_str_opt(subject),
        body_html=_form_str_opt(body_html),
        body_text=_form_str_opt(body_text),
        campaign_sender_id=sender.id if selected_channel == CampaignChannel.email and sender else None,
        campaign_smtp_config_id=smtp_profile.id if selected_channel == CampaignChannel.email and smtp_profile else None,
        connector_config_id=whatsapp_connector.id
        if selected_channel == CampaignChannel.whatsapp and whatsapp_connector
        else None,
        from_name=sender.from_name if selected_channel == CampaignChannel.email and sender else None,
        from_email=sender.from_email if selected_channel == CampaignChannel.email and sender else None,
        reply_to=sender.reply_to if selected_channel == CampaignChannel.email and sender else None,
        whatsapp_template_name=wa_template_name if selected_channel == CampaignChannel.whatsapp else None,
        whatsapp_template_language=wa_template_lang if selected_channel == CampaignChannel.whatsapp else None,
        whatsapp_template_components=wa_template_components if selected_channel == CampaignChannel.whatsapp else None,
        segment_filter=segment_filter,
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
    campaign = campaigns_service.get(db, campaign_id)
    stats = Campaigns.analytics(db, campaign_id)
    recipients = recipients_service.list(db, campaign_id, limit=20, offset=0)

    # Load person names for recipients
    person_ids = [r.person_id for r in recipients]
    persons = db.query(Person).filter(Person.id.in_(person_ids)).all() if person_ids else []
    person_map = {str(p.id): p for p in persons}

    steps = steps_service.list(db, campaign_id) if campaign.campaign_type == CampaignType.nurture else []

    ctx = _base_ctx(
        request,
        db,
        campaign=campaign,
        stats=stats,
        recipients=recipients,
        person_map=person_map,
        steps=steps,
    )
    return templates.TemplateResponse("admin/crm/campaign_detail.html", ctx)


# ── Edit ──────────────────────────────────────────────────────────────────────


@router.get("/{campaign_id}/edit", response_class=HTMLResponse)
def campaign_edit_form(
    request: Request,
    campaign_id: str,
    db: Session = Depends(_get_db),
):
    campaign = campaigns_service.get(db, campaign_id)
    ctx = _base_ctx(
        request,
        db,
        campaign=campaign,
        campaign_types=CampaignType,
        campaign_channels=CampaignChannel,
        party_statuses=PartyStatus,
        region_options=REGION_OPTIONS,
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
    seg_active_status: str = Form("active"),
    seg_created_after: str = Form(""),
    seg_created_before: str = Form(""),
    whatsapp_template_name: str = Form(""),
    whatsapp_template_language: str = Form(""),
    whatsapp_template_components: str = Form(""),
):
    try:
        ct = CampaignType(campaign_type)
    except ValueError:
        ct = CampaignType.one_time
    try:
        selected_channel = CampaignChannel(channel)
    except ValueError:
        selected_channel = CampaignChannel.email

    segment_filter = _build_segment_filter(
        seg_party_status,
        seg_regions,
        seg_active_status,
        seg_created_after,
        seg_created_before,
    )

    # Parse WhatsApp template components JSON
    wa_template_name = _form_str_opt(whatsapp_template_name)
    wa_template_lang = _form_str_opt(whatsapp_template_language)
    wa_template_components: dict | None = None
    if whatsapp_template_components.strip():
        try:
            wa_template_components = json.loads(whatsapp_template_components)
        except (json.JSONDecodeError, TypeError):
            wa_template_components = None

    errors: list[str] = []
    sender = None
    sender_id_value = campaign_sender_id.strip()
    smtp_profile = None
    smtp_id_value = campaign_smtp_config_id.strip()
    whatsapp_connector = None
    whatsapp_connector_id_value = whatsapp_connector_id.strip()

    if selected_channel == CampaignChannel.email:
        if sender_id_value:
            try:
                sender = campaign_senders.get(db, sender_id_value)
            except HTTPException as exc:
                errors.append(str(exc.detail))
        else:
            errors.append("Campaign sender is required.")

        if sender and not sender.is_active:
            errors.append("Selected campaign sender is inactive.")

        if smtp_id_value:
            try:
                smtp_profile = campaign_smtp_configs.get(db, smtp_id_value)
            except HTTPException as exc:
                errors.append(str(exc.detail))
        else:
            errors.append("Campaign SMTP profile is required.")

        if smtp_profile and not smtp_profile.is_active:
            errors.append("Selected campaign SMTP profile is inactive.")
    else:
        if whatsapp_connector_id_value:
            try:
                whatsapp_connector = db.get(ConnectorConfig, UUID(whatsapp_connector_id_value))
            except ValueError:
                errors.append("Invalid WhatsApp connector.")
                whatsapp_connector = None
            if not whatsapp_connector and "Invalid WhatsApp connector." not in errors:
                errors.append("WhatsApp connector not found.")
            elif whatsapp_connector and whatsapp_connector.connector_type != ConnectorType.whatsapp:
                errors.append("Selected connector is not a WhatsApp connector.")
            elif whatsapp_connector and not whatsapp_connector.is_active:
                errors.append("Selected WhatsApp connector is inactive.")
        else:
            errors.append("WhatsApp connector is required.")

    if errors:
        campaign_stub = SimpleNamespace(
            id=campaign_id,
            name=name,
            campaign_type=ct,
            channel=selected_channel,
            subject=subject or None,
            body_html=body_html or None,
            body_text=body_text or None,
            from_name=None,
            from_email=None,
            reply_to=None,
            segment_filter=segment_filter,
            campaign_sender_id=sender_id_value or None,
            campaign_smtp_config_id=smtp_id_value or None,
            connector_config_id=whatsapp_connector_id_value or None,
            whatsapp_template_name=wa_template_name,
            whatsapp_template_language=wa_template_lang,
            whatsapp_template_components=wa_template_components,
        )
        ctx = _base_ctx(
            request,
            db,
            campaign=campaign_stub,
            campaign_types=CampaignType,
            campaign_channels=CampaignChannel,
            party_statuses=PartyStatus,
            region_options=REGION_OPTIONS,
            campaign_senders=_load_campaign_senders(db),
            campaign_smtp_profiles=_load_campaign_smtp_profiles(db),
            whatsapp_connectors=_load_whatsapp_connectors(db),
            errors=errors,
        )
        return templates.TemplateResponse("admin/crm/campaign_form.html", ctx, status_code=400)

    payload = CampaignUpdate(
        name=name,
        campaign_type=ct,
        channel=selected_channel,
        segment_filter=segment_filter,
        subject=_form_str_opt(subject),
        body_html=_form_str_opt(body_html),
        body_text=_form_str_opt(body_text),
        campaign_sender_id=sender.id if selected_channel == CampaignChannel.email and sender else None,
        campaign_smtp_config_id=smtp_profile.id if selected_channel == CampaignChannel.email and smtp_profile else None,
        connector_config_id=whatsapp_connector.id
        if selected_channel == CampaignChannel.whatsapp and whatsapp_connector
        else None,
        from_name=sender.from_name if selected_channel == CampaignChannel.email and sender else None,
        from_email=sender.from_email if selected_channel == CampaignChannel.email and sender else None,
        reply_to=sender.reply_to if selected_channel == CampaignChannel.email and sender else None,
        whatsapp_template_name=wa_template_name if selected_channel == CampaignChannel.whatsapp else None,
        whatsapp_template_language=wa_template_lang if selected_channel == CampaignChannel.whatsapp else None,
        whatsapp_template_components=wa_template_components if selected_channel == CampaignChannel.whatsapp else None,
    )
    campaigns_service.update(db, campaign_id, payload)
    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)


# ── Delete ────────────────────────────────────────────────────────────────────


@router.post("/{campaign_id}/delete")
def campaign_delete(
    campaign_id: str,
    db: Session = Depends(_get_db),
):
    campaigns_service.delete(db, campaign_id)
    return RedirectResponse(url="/admin/crm/campaigns", status_code=303)


# ── Actions ───────────────────────────────────────────────────────────────────


@router.post("/{campaign_id}/schedule")
def campaign_schedule(
    campaign_id: str,
    db: Session = Depends(_get_db),
    scheduled_at: str = Form(...),
):
    dt = _parse_datetime_opt(scheduled_at)
    if not dt:
        return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)
    campaigns_service.schedule(db, campaign_id, dt)
    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)


@router.post("/{campaign_id}/send")
def campaign_send_now(
    campaign_id: str,
    db: Session = Depends(_get_db),
):
    campaigns_service.send_now(db, campaign_id)
    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)


@router.post("/{campaign_id}/cancel")
def campaign_cancel(
    campaign_id: str,
    db: Session = Depends(_get_db),
):
    campaigns_service.cancel(db, campaign_id)
    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)


# ── Preview ───────────────────────────────────────────────────────────────────


@router.get("/{campaign_id}/preview", response_class=HTMLResponse)
def campaign_preview(
    request: Request,
    campaign_id: str,
    db: Session = Depends(_get_db),
):
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
    connector_id: str | None = Query(None),
    db: Session = Depends(_get_db),
):
    from app.services.crm.inbox.whatsapp_templates import list_whatsapp_templates

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
    campaign = campaigns_service.get(db, campaign_id)
    steps = steps_service.list(db, campaign_id)
    ctx = _base_ctx(request, db, campaign=campaign, steps=steps)
    return templates.TemplateResponse("admin/crm/_campaign_steps.html", ctx)


@router.post("/{campaign_id}/steps")
def campaign_step_create(
    campaign_id: str,
    db: Session = Depends(_get_db),
    name: str = Form(""),
    subject: str = Form(""),
    body_html: str = Form(""),
    body_text: str = Form(""),
    delay_days: int = Form(0),
    step_index: int = Form(0),
):
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
    campaign_id: str,
    step_id: str,
    db: Session = Depends(_get_db),
):
    steps_service.delete(db, step_id)
    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)
