"""CRM sales dashboard and pipeline settings routes."""

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.crm.web_sales import (
    bulk_assign_pipeline_leads,
    create_pipeline,
    create_pipeline_stage,
    delete_pipeline,
    disable_pipeline_stage,
    pipeline_create_error_data,
    pipeline_edit_data,
    pipeline_new_data,
    pipeline_settings_data,
    pipeline_update_error_data,
    sales_dashboard_data,
    sales_pipeline_data,
    update_pipeline,
    update_pipeline_stage,
)

router = APIRouter(tags=["web-admin-crm"])
templates = Jinja2Templates(directory="templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _crm_base_context(*args, **kwargs):
    from app.web.admin.crm import _crm_base_context as _shared_crm_base_context

    return _shared_crm_base_context(*args, **kwargs)


def _can_write_sales(request: Request) -> bool:
    from app.web.admin.crm import _can_write_sales as _shared_can_write_sales

    return _shared_can_write_sales(request)


@router.get("/sales", response_class=HTMLResponse)
def crm_sales_dashboard(
    request: Request,
    pipeline_id: str | None = None,
    period_days: int = Query(30, ge=7, le=365),
    db: Session = Depends(get_db),
):
    context = _crm_base_context(request, db, "sales")
    context.update(sales_dashboard_data(db, pipeline_id=pipeline_id, period_days=period_days))
    return templates.TemplateResponse("admin/crm/sales_dashboard.html", context)


@router.get("/sales/pipeline", response_class=HTMLResponse)
def crm_sales_pipeline(
    request: Request,
    pipeline_id: str | None = None,
    db: Session = Depends(get_db),
):
    context = _crm_base_context(request, db, "sales")
    context.update(sales_pipeline_data(db, pipeline_id=pipeline_id))
    return templates.TemplateResponse("admin/crm/sales_pipeline.html", context)


@router.get("/settings/pipelines/new", response_class=HTMLResponse)
def crm_pipeline_new(
    request: Request,
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    context = _crm_base_context(request, db, "sales")
    context.update(pipeline_new_data())
    return templates.TemplateResponse("admin/crm/pipeline_form.html", context)


@router.post("/settings/pipelines", response_class=HTMLResponse)
def crm_pipeline_create(
    request: Request,
    name: str | None = Form(None),
    is_active: str | None = Form(None),
    create_default_stages: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    try:
        pipeline_id = create_pipeline(
            db,
            name=name,
            is_active=is_active,
            create_default_stages=create_default_stages,
        )
        return RedirectResponse(url=f"/admin/crm/sales/pipeline?pipeline_id={pipeline_id}", status_code=303)
    except (ValidationError, ValueError) as exc:
        error = str(exc)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)

    context = _crm_base_context(request, db, "sales")
    context.update(pipeline_create_error_data(name, is_active, create_default_stages))
    context["error"] = error
    return templates.TemplateResponse("admin/crm/pipeline_form.html", context)


@router.get("/settings/pipelines", response_class=HTMLResponse)
def crm_pipeline_settings(
    request: Request,
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)

    context = _crm_base_context(request, db, "sales")
    context.update(
        pipeline_settings_data(
            db,
            bulk_result=request.query_params.get("bulk_result", "").strip(),
            bulk_count=request.query_params.get("bulk_count", "").strip(),
        )
    )
    return templates.TemplateResponse("admin/crm/pipeline_settings.html", context)


@router.get("/settings/pipelines/{pipeline_id}/edit", response_class=HTMLResponse)
def crm_pipeline_edit(
    request: Request,
    pipeline_id: str,
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)

    context = _crm_base_context(request, db, "sales")
    context.update(pipeline_edit_data(db, pipeline_id=pipeline_id))
    return templates.TemplateResponse("admin/crm/pipeline_form.html", context)


@router.post("/settings/pipelines/{pipeline_id}", response_class=HTMLResponse)
def crm_pipeline_update(
    request: Request,
    pipeline_id: str,
    name: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)

    try:
        update_pipeline(db, pipeline_id=pipeline_id, name=name, is_active=is_active)
        return RedirectResponse(url="/admin/crm/settings/pipelines", status_code=303)
    except (ValidationError, ValueError) as exc:
        error = str(exc)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)

    context = _crm_base_context(request, db, "sales")
    context.update(pipeline_update_error_data(pipeline_id=pipeline_id, name=name, is_active=is_active))
    context["error"] = error
    return templates.TemplateResponse("admin/crm/pipeline_form.html", context, status_code=400)


@router.post("/settings/pipelines/{pipeline_id}/delete")
def crm_pipeline_delete(
    request: Request,
    pipeline_id: str,
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    delete_pipeline(db, pipeline_id)
    return RedirectResponse(url="/admin/crm/settings/pipelines", status_code=303)


@router.post("/settings/pipelines/{pipeline_id}/stages")
def crm_pipeline_stage_create(
    request: Request,
    pipeline_id: str,
    name: str = Form(...),
    order_index: int = Form(0),
    default_probability: int = Form(50),
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    create_pipeline_stage(
        db,
        pipeline_id=pipeline_id,
        name=name,
        order_index=order_index,
        default_probability=default_probability,
    )
    return RedirectResponse(url="/admin/crm/settings/pipelines", status_code=303)


@router.post("/settings/pipelines/stages/{stage_id}")
def crm_pipeline_stage_update(
    request: Request,
    stage_id: str,
    name: str = Form(...),
    order_index: int = Form(0),
    default_probability: int = Form(50),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    update_pipeline_stage(
        db,
        stage_id=stage_id,
        name=name,
        order_index=order_index,
        default_probability=default_probability,
        is_active=is_active,
    )
    return RedirectResponse(url="/admin/crm/settings/pipelines", status_code=303)


@router.post("/settings/pipelines/stages/{stage_id}/delete")
def crm_pipeline_stage_delete(
    request: Request,
    stage_id: str,
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    disable_pipeline_stage(db, stage_id=stage_id)
    return RedirectResponse(url="/admin/crm/settings/pipelines", status_code=303)


@router.post("/settings/pipelines/{pipeline_id}/bulk-assign-leads")
def crm_pipeline_bulk_assign_leads(
    request: Request,
    pipeline_id: str,
    stage_id: str | None = Form(None),
    scope: str = Form("unassigned"),
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    count = bulk_assign_pipeline_leads(db, pipeline_id=pipeline_id, stage_id=stage_id, scope=scope)
    return RedirectResponse(
        url=f"/admin/crm/settings/pipelines?bulk_result=ok&bulk_count={count}",
        status_code=303,
    )
