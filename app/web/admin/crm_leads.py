"""CRM lead routes."""

from html import escape as html_escape

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.logging import get_logger
from app.services.auth_dependencies import require_permission
from app.services.crm.web_leads import (
    LeadUpsertInput,
    create_lead,
    delete_lead,
    edit_lead_form_data,
    lead_detail_data,
    lead_form_error_data,
    list_leads_page_data,
    new_lead_form_data,
    update_lead,
    update_lead_status,
)

router = APIRouter(tags=["web-admin-crm"])
templates = Jinja2Templates(directory="templates")
logger = get_logger(__name__)
REGION_OPTIONS = ["Gudu", "Garki", "Gwarimpa", "Jabi", "Lagos"]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _crm_base_context(*args, **kwargs):
    from app.web.admin.crm import _crm_base_context as _shared_crm_base_context

    return _shared_crm_base_context(*args, **kwargs)


def _load_crm_sales_options(db: Session):
    from app.web.admin.crm import _load_crm_sales_options as _shared_load_crm_sales_options

    return _shared_load_crm_sales_options(db)


def _load_pipeline_stages_for_pipeline(db: Session, pipeline_id: str | None):
    from app.web.admin.crm import _load_pipeline_stages_for_pipeline as _shared_load_pipeline_stages_for_pipeline

    return _shared_load_pipeline_stages_for_pipeline(db, pipeline_id)


def _can_write_sales(request: Request) -> bool:
    from app.web.admin.crm import _can_write_sales as _shared_can_write_sales

    return _shared_can_write_sales(request)


@router.get(
    "/leads",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:read"))],
)
def crm_leads_list(
    request: Request,
    status: str | None = None,
    pipeline_id: str | None = None,
    stage_id: str | None = None,
    owner_agent_id: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    db: Session = Depends(get_db),
):
    options = _load_crm_sales_options(db)
    context = _crm_base_context(request, db, "leads")
    context.update(
        list_leads_page_data(
            db,
            status=status,
            pipeline_id=pipeline_id,
            stage_id=stage_id,
            owner_agent_id=owner_agent_id,
            page=page,
            per_page=per_page,
            options=options,
            can_write_leads=_can_write_sales(request),
        )
    )
    return templates.TemplateResponse("admin/crm/leads.html", context)


@router.get(
    "/leads/new",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:write"))],
)
def crm_lead_new(request: Request, db: Session = Depends(get_db)):
    from app.web.admin import get_current_user

    person_id = request.query_params.get("person_id", "").strip()
    contact_id = request.query_params.get("contact_id", "").strip()
    pipeline_id = request.query_params.get("pipeline_id", "").strip()

    options = _load_crm_sales_options(db)
    current_user = get_current_user(request)
    context = _crm_base_context(request, db, "leads")
    context.update(
        new_lead_form_data(
            db,
            person_id=person_id,
            contact_id=contact_id,
            pipeline_id=pipeline_id,
            current_person_id=(current_user or {}).get("person_id"),
            options=options,
            load_pipeline_stages=_load_pipeline_stages_for_pipeline,
        )
    )
    context.update(
        {
            "form_title": "New Lead",
            "submit_label": "Create Lead",
            "action_url": "/admin/crm/leads",
            "region_options": REGION_OPTIONS,
        }
    )
    return templates.TemplateResponse("admin/crm/lead_form.html", context)


@router.get(
    "/leads/{lead_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:read"))],
)
def crm_lead_detail(request: Request, lead_id: str, db: Session = Depends(get_db)):
    options = _load_crm_sales_options(db)
    context = _crm_base_context(request, db, "leads")
    context.update(
        lead_detail_data(
            db,
            lead_id=lead_id,
            options=options,
            can_write_leads=_can_write_sales(request),
        )
    )
    return templates.TemplateResponse("admin/crm/lead_detail.html", context)


@router.post(
    "/leads/{lead_id}/status",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:write"))],
)
async def crm_lead_status_update(
    request: Request,
    lead_id: str,
    db: Session = Depends(get_db),
):
    """Quick inline status update for a lead."""
    form = await request.form()
    status_raw = form.get("status")
    status_value = status_raw.strip() if isinstance(status_raw, str) else ""
    try:
        update_lead_status(db, lead_id=lead_id, status_value=status_value)
        if request.headers.get("HX-Request"):
            return HTMLResponse(content="", headers={"HX-Redirect": f"/admin/crm/leads/{lead_id}"})
        return RedirectResponse(f"/admin/crm/leads/{lead_id}", status_code=303)
    except Exception as exc:
        error = html_escape(exc.detail if hasattr(exc, "detail") else str(exc))
        if request.headers.get("HX-Request"):
            return HTMLResponse(content=f'<p class="text-red-600 text-sm">{error}</p>', status_code=422)
        return RedirectResponse(f"/admin/crm/leads/{lead_id}", status_code=303)


@router.post(
    "/leads",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:write"))],
)
def crm_lead_create(
    request: Request,
    person_id: str | None = Form(None),
    contact_id: str | None = Form(None),
    pipeline_id: str | None = Form(None),
    stage_id: str | None = Form(None),
    owner_agent_id: str | None = Form(None),
    title: str | None = Form(None),
    status: str | None = Form(None),
    estimated_value: str | None = Form(None),
    currency: str | None = Form(None),
    probability: str | None = Form(None),
    expected_close_date: str | None = Form(None),
    lost_reason: str | None = Form(None),
    region: str | None = Form(None),
    address: str | None = Form(None),
    notes: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_current_user

    form_input = LeadUpsertInput(
        person_id=person_id,
        contact_id=contact_id,
        pipeline_id=pipeline_id,
        stage_id=stage_id,
        owner_agent_id=owner_agent_id,
        title=title,
        status=status,
        estimated_value=estimated_value,
        currency=currency,
        probability=probability,
        expected_close_date=expected_close_date,
        lost_reason=lost_reason,
        region=region,
        address=address,
        notes=notes,
        is_active=is_active,
    )
    try:
        current_user = get_current_user(request)
        create_lead(
            db,
            form=form_input,
            current_person_id=(current_user or {}).get("person_id"),
            load_pipeline_stages=_load_pipeline_stages_for_pipeline,
        )
        return RedirectResponse(url="/admin/crm/leads", status_code=303)
    except (ValidationError, ValueError) as exc:
        error = str(exc)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)

    options = _load_crm_sales_options(db)
    context = _crm_base_context(request, db, "leads")
    context.update(lead_form_error_data(form=form_input, mode="create", lead_id=None, options=options))
    context.update(
        {
            "form_title": "New Lead",
            "submit_label": "Create Lead",
            "action_url": "/admin/crm/leads",
            "error": error,
            "region_options": REGION_OPTIONS,
        }
    )
    return templates.TemplateResponse("admin/crm/lead_form.html", context, status_code=400)


@router.get(
    "/leads/{lead_id}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:write"))],
)
def crm_lead_edit(request: Request, lead_id: str, db: Session = Depends(get_db)):
    options = _load_crm_sales_options(db)
    context = _crm_base_context(request, db, "leads")
    context.update(
        edit_lead_form_data(
            db,
            lead_id=lead_id,
            options=options,
            load_pipeline_stages=_load_pipeline_stages_for_pipeline,
        )
    )
    context.update(
        {
            "form_title": "Edit Lead",
            "submit_label": "Save Lead",
            "action_url": f"/admin/crm/leads/{lead_id}/edit",
            "region_options": REGION_OPTIONS,
        }
    )
    return templates.TemplateResponse("admin/crm/lead_form.html", context)


@router.post(
    "/leads/{lead_id}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:write"))],
)
def crm_lead_update(
    request: Request,
    lead_id: str,
    person_id: str | None = Form(None),
    contact_id: str | None = Form(None),
    pipeline_id: str | None = Form(None),
    stage_id: str | None = Form(None),
    owner_agent_id: str | None = Form(None),
    title: str | None = Form(None),
    status: str | None = Form(None),
    estimated_value: str | None = Form(None),
    currency: str | None = Form(None),
    probability: str | None = Form(None),
    expected_close_date: str | None = Form(None),
    lost_reason: str | None = Form(None),
    region: str | None = Form(None),
    address: str | None = Form(None),
    notes: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    form_input = LeadUpsertInput(
        person_id=person_id,
        contact_id=contact_id,
        pipeline_id=pipeline_id,
        stage_id=stage_id,
        owner_agent_id=owner_agent_id,
        title=title,
        status=status,
        estimated_value=estimated_value,
        currency=currency,
        probability=probability,
        expected_close_date=expected_close_date,
        lost_reason=lost_reason,
        region=region,
        address=address,
        notes=notes,
        is_active=is_active,
    )
    try:
        update_lead(
            db,
            lead_id=lead_id,
            form=form_input,
            load_pipeline_stages=_load_pipeline_stages_for_pipeline,
        )
        return RedirectResponse(url="/admin/crm/leads", status_code=303)
    except (ValidationError, ValueError) as exc:
        error = str(exc)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)

    options = _load_crm_sales_options(db)
    context = _crm_base_context(request, db, "leads")
    context.update(lead_form_error_data(form=form_input, mode="update", lead_id=lead_id, options=options))
    context.update(
        {
            "form_title": "Edit Lead",
            "submit_label": "Save Lead",
            "action_url": f"/admin/crm/leads/{lead_id}/edit",
            "error": error,
            "region_options": REGION_OPTIONS,
        }
    )
    return templates.TemplateResponse("admin/crm/lead_form.html", context, status_code=400)


@router.post(
    "/leads/{lead_id}/delete",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:write"))],
)
def crm_lead_delete(request: Request, lead_id: str, db: Session = Depends(get_db)):
    _ = request
    delete_lead(db, lead_id)
    return RedirectResponse(url="/admin/crm/leads", status_code=303)
