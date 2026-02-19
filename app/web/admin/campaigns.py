"""Admin campaign management web routes."""

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.crm.campaign_permissions import can_view_campaigns, can_write_campaigns
from app.services.crm.web_campaigns import (
    CampaignUpsertInput,
    build_campaign_form_stub,
    campaign_detail_page_data,
    campaign_form_page_data,
    campaign_list_page_data,
    campaign_preview_audience_data,
    campaign_preview_page_data,
    campaign_recipients_table_data,
    campaign_steps_page_data,
    campaign_whatsapp_templates_payload,
    cancel_campaign,
    create_campaign,
    create_campaign_step,
    delete_campaign,
    delete_campaign_step,
    get_campaign,
    resolve_campaign_upsert,
    schedule_campaign_from_form,
    send_campaign_now,
    update_campaign,
    update_campaign_step,
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
    ctx = _base_ctx(
        request,
        db,
        **campaign_list_page_data(
            db,
            status=status,
            search=search,
            order_by=order_by,
            order_dir=order_dir,
        ),
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
        **campaign_form_page_data(
            db,
            campaign=None,
            errors=[],
            region_options=REGION_OPTIONS,
        ),
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
            **campaign_form_page_data(
                db,
                campaign=campaign_stub,
                errors=resolved.errors,
                region_options=REGION_OPTIONS,
            ),
        )
        return templates.TemplateResponse("admin/crm/campaign_form.html", ctx, status_code=400)
    campaign = create_campaign(
        db,
        name=name,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        resolved=resolved,
        created_by_id=created_by_id,
    )
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
    ctx = _base_ctx(
        request,
        db,
        **campaign_form_page_data(
            db,
            campaign=get_campaign(db, campaign_id=campaign_id),
            errors=[],
            region_options=REGION_OPTIONS,
        ),
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
            **campaign_form_page_data(
                db,
                campaign=campaign_stub,
                errors=resolved.errors,
                region_options=REGION_OPTIONS,
            ),
        )
        return templates.TemplateResponse("admin/crm/campaign_form.html", ctx, status_code=400)
    update_campaign(
        db,
        campaign_id=campaign_id,
        name=name,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        resolved=resolved,
    )
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
    delete_campaign(db, campaign_id=campaign_id)
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
    if not schedule_campaign_from_form(
        db,
        campaign_id=campaign_id,
        scheduled_at=scheduled_at,
    ):
        return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)
    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)


@router.post("/{campaign_id}/send")
def campaign_send_now(
    request: Request,
    campaign_id: str,
    db: Session = Depends(_get_db),
):
    if not can_write_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    send_campaign_now(db, campaign_id=campaign_id)
    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)


@router.post("/{campaign_id}/cancel")
def campaign_cancel(
    request: Request,
    campaign_id: str,
    db: Session = Depends(_get_db),
):
    if not can_write_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return _forbidden_html()
    cancel_campaign(db, campaign_id=campaign_id)
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
    ctx = _base_ctx(request, db, **campaign_preview_page_data(db, campaign_id=campaign_id))
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
    ctx = _base_ctx(request, db, **campaign_preview_audience_data(db, campaign_id=campaign_id))
    return templates.TemplateResponse("admin/crm/_campaign_audience_preview.html", ctx)


@router.get("/templates/whatsapp", response_class=JSONResponse)
def campaign_whatsapp_templates(
    request: Request,
    connector_id: str | None = Query(None),
    db: Session = Depends(_get_db),
):
    if not can_write_campaigns(_get_current_roles(request), _get_current_scopes(request)):
        return JSONResponse({"templates": [], "error": "Forbidden"}, status_code=403)
    payload, status_code = campaign_whatsapp_templates_payload(db, connector_id=connector_id)
    return JSONResponse(payload, status_code=status_code)


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
    ctx = _base_ctx(
        request,
        db,
        **campaign_recipients_table_data(
            db,
            campaign_id=campaign_id,
            status=status,
            offset=offset,
        ),
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
    ctx = _base_ctx(request, db, **campaign_steps_page_data(db, campaign_id=campaign_id))
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
    create_campaign_step(
        db,
        campaign_id=campaign_id,
        name=name,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        delay_days=delay_days,
        step_index=step_index,
    )
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
    update_campaign_step(
        db,
        step_id=step_id,
        name=name,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        delay_days=delay_days,
        step_index=step_index,
    )
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
    delete_campaign_step(db, step_id=step_id)
    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign_id}", status_code=303)
