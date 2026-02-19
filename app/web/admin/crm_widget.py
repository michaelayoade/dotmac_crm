"""CRM widget routes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.crm.web_widget import (
    widget_create_payload_from_form,
    widget_detail_data,
    widget_list_data,
    widget_update_payload_from_form,
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


@router.get("/widget", response_class=HTMLResponse)
def crm_widget_list(
    request: Request,
    db: Session = Depends(get_db),
):
    """List all chat widget configurations."""
    context = _crm_base_context(request, db, "widget")
    context.update(widget_list_data(db))
    context.update(
        {
            "success_message": request.query_params.get("success"),
            "error_message": request.query_params.get("error"),
        }
    )
    return templates.TemplateResponse("admin/crm/widget_list.html", context)


@router.get("/widget/new", response_class=HTMLResponse)
def crm_widget_new(
    request: Request,
    db: Session = Depends(get_db),
):
    """Show widget creation form."""
    context = _crm_base_context(request, db, "widget")
    context.update({"widget": None})
    return templates.TemplateResponse("admin/crm/widget_detail.html", context)


@router.post("/widget", response_class=HTMLResponse)
async def crm_widget_create(
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a new widget configuration."""
    from app.services.crm.chat_widget import widget_configs

    form = await request.form()
    try:
        payload = widget_create_payload_from_form(form)
        widget = widget_configs.create(db, payload)
        return RedirectResponse(
            url=f"/admin/crm/widget/{widget.id}?success=Widget created successfully",
            status_code=303,
        )
    except Exception as exc:
        context = _crm_base_context(request, db, "widget")
        context.update({"widget": None, "error_message": str(exc)})
        return templates.TemplateResponse("admin/crm/widget_detail.html", context)


@router.get("/widget/{widget_id}", response_class=HTMLResponse)
def crm_widget_detail(
    widget_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Widget detail with settings and embed code."""
    from app.services.crm.chat_widget import widget_configs

    widget = widget_configs.get(db, widget_id)
    if not widget:
        return RedirectResponse(
            url="/admin/crm/widget?error=Widget not found",
            status_code=303,
        )

    host = request.headers.get("host", "localhost:8000")
    scheme = request.headers.get("x-forwarded-proto", "http")
    base_url = f"{scheme}://{host}"

    detail_data = widget_detail_data(db, widget=widget, base_url=base_url)
    if not detail_data:
        return RedirectResponse(
            url="/admin/crm/widget?error=Widget not found",
            status_code=303,
        )

    context = _crm_base_context(request, db, "widget")
    context.update(detail_data)
    context.update(
        {
            "success_message": request.query_params.get("success"),
            "error_message": request.query_params.get("error"),
        }
    )
    return templates.TemplateResponse("admin/crm/widget_detail.html", context)


@router.post("/widget/{widget_id}", response_class=HTMLResponse)
async def crm_widget_update(
    widget_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Update widget configuration."""
    from app.services.crm.chat_widget import widget_configs

    form = await request.form()
    try:
        payload = widget_update_payload_from_form(form)
        widget = widget_configs.update(db, widget_id, payload)
        if not widget:
            return RedirectResponse(
                url="/admin/crm/widget?error=Widget not found",
                status_code=303,
            )

        return RedirectResponse(
            url=f"/admin/crm/widget/{widget_id}?success=Widget updated successfully",
            status_code=303,
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/admin/crm/widget/{widget_id}?error={exc!s}",
            status_code=303,
        )


@router.post("/widget/{widget_id}/delete", response_class=HTMLResponse)
def crm_widget_delete(
    widget_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Delete a widget configuration."""
    from app.services.crm.chat_widget import widget_configs

    _ = request
    if widget_configs.delete(db, widget_id):
        return RedirectResponse(
            url="/admin/crm/widget?success=Widget deleted successfully",
            status_code=303,
        )
    return RedirectResponse(
        url="/admin/crm/widget?error=Widget not found",
        status_code=303,
    )
