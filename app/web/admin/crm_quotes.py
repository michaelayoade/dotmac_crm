"""CRM quote routes."""

from html import escape as html_escape

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.logging import get_logger
from app.services.audit_helpers import build_changes_metadata, log_audit_event
from app.services.crm.web_quotes import (
    QuoteUpsertInput,
    bulk_delete,
    bulk_status,
    create_quote,
    delete_quote,
    edit_quote_form_data,
    list_quotes_page_data,
    new_quote_form_data,
    quote_detail_data,
    quote_form_error_data,
    quote_pdf_response,
    quote_preview_response,
    send_quote_summary,
    update_quote,
    update_quote_status,
)

router = APIRouter(tags=["web-admin-crm"])
templates = Jinja2Templates(directory="templates")
logger = get_logger(__name__)


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


def _format_project_summary(*args, **kwargs):
    from app.web.admin.crm import _format_project_summary as _shared_format_project_summary

    return _shared_format_project_summary(*args, **kwargs)


def _build_quote_pdf_bytes(*args, **kwargs):
    from app.web.admin.crm import _build_quote_pdf_bytes as _shared_build_quote_pdf_bytes

    return _shared_build_quote_pdf_bytes(*args, **kwargs)


def _apply_message_attachments(*args, **kwargs):
    from app.web.admin.crm import _apply_message_attachments as _shared_apply_message_attachments

    return _shared_apply_message_attachments(*args, **kwargs)


def _resolve_brand_logo_src(*args, **kwargs):
    from app.web.admin.crm import _resolve_brand_logo_src as _shared_resolve_brand_logo_src

    return _shared_resolve_brand_logo_src(*args, **kwargs)


def _ensure_pydyf_compat():
    from app.web.admin.crm import _ensure_pydyf_compat as _shared_ensure_pydyf_compat

    return _shared_ensure_pydyf_compat()


def _require_admin_role(request: Request):
    from app.web.admin.crm import _require_admin_role as _shared_require_admin_role

    return _shared_require_admin_role(request)


def _billing_service():
    from app.web.admin.crm import billing_service as _shared_billing_service

    return _shared_billing_service


@router.get("/quotes", response_class=HTMLResponse)
def crm_quotes_list(
    request: Request,
    status: str | None = None,
    lead_id: str | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=200),
    db: Session = Depends(get_db),
):
    options = _load_crm_sales_options(db)
    context = _crm_base_context(request, db, "quotes")
    context.update(
        list_quotes_page_data(
            db,
            status=status,
            lead_id=lead_id,
            search=search,
            page=page,
            per_page=per_page,
            contacts=options["contacts"],
        )
    )
    # HTMX partial response for table
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("admin/crm/_quotes_table.html", context)
    return templates.TemplateResponse("admin/crm/quotes.html", context)


@router.get("/quotes/new", response_class=HTMLResponse)
def crm_quote_new(
    request: Request,
    lead_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    options = _load_crm_sales_options(db)
    tax_rates = _billing_service().tax_rates.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    context = _crm_base_context(request, db, "quotes")
    context.update(
        new_quote_form_data(
            db,
            lead_id=lead_id,
            contacts=options["contacts"],
            tax_rates=tax_rates,
        )
    )
    context.update({"form_title": "New Quote", "submit_label": "Create Quote", "action_url": "/admin/crm/quotes"})
    return templates.TemplateResponse("admin/crm/quote_form.html", context)


@router.get("/quotes/{quote_id}", response_class=HTMLResponse)
def crm_quote_detail(request: Request, quote_id: str, db: Session = Depends(get_db)):
    context = _crm_base_context(request, db, "quotes")
    context.update(quote_detail_data(db, quote_id=quote_id, format_project_summary=_format_project_summary))
    return templates.TemplateResponse("admin/crm/quote_detail.html", context)


@router.post("/quotes/{quote_id}/status", response_class=HTMLResponse)
async def crm_quote_status_update(
    request: Request,
    quote_id: str,
    db: Session = Depends(get_db),
):
    """Quick inline status update for a quote."""
    form = await request.form()
    status_raw = form.get("status")
    status_value = status_raw.strip() if isinstance(status_raw, str) else ""
    try:
        update_quote_status(db, quote_id=quote_id, status_value=status_value)
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                content="",
                headers={"HX-Redirect": f"/admin/crm/quotes/{quote_id}"},
            )
        return RedirectResponse(f"/admin/crm/quotes/{quote_id}", status_code=303)
    except Exception as exc:
        error = html_escape(exc.detail if hasattr(exc, "detail") else str(exc))
        if request.headers.get("HX-Request"):
            return HTMLResponse(content=f'<p class="text-red-600 text-sm">{error}</p>', status_code=422)
        return RedirectResponse(f"/admin/crm/quotes/{quote_id}", status_code=303)


@router.post("/quotes/{quote_id}/send-summary", response_class=HTMLResponse)
def crm_quote_send_summary(
    request: Request,
    quote_id: str,
    channel_type: str = Form(...),
    message: str = Form(...),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    author_id = current_user.get("person_id") if current_user else None
    sent = send_quote_summary(
        db,
        request=request,
        quote_id=quote_id,
        channel_type=channel_type,
        message=message,
        author_id=author_id,
        message_attachment_max_size_bytes=settings.message_attachment_max_size_bytes,
        format_project_summary=_format_project_summary,
        build_quote_pdf_bytes=_build_quote_pdf_bytes,
        apply_message_attachments=_apply_message_attachments,
    )
    if not sent:
        return RedirectResponse(
            url=f"/admin/crm/quotes/{quote_id}?send_error=1",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/admin/crm/quotes/{quote_id}?send=1",
        status_code=303,
    )


@router.get("/quotes/{quote_id}/pdf")
def crm_quote_pdf(request: Request, quote_id: str, db: Session = Depends(get_db)):
    return quote_pdf_response(
        db,
        request=request,
        quote_id=quote_id,
        templates=templates,
        logger=logger,
        resolve_brand_logo_src=_resolve_brand_logo_src,
        ensure_pydyf_compat=_ensure_pydyf_compat,
    )


@router.get("/quotes/{quote_id}/preview", response_class=HTMLResponse)
def crm_quote_preview(request: Request, quote_id: str):
    return quote_preview_response(request, quote_id=quote_id)


@router.post("/quotes", response_class=HTMLResponse)
def crm_quote_create(
    request: Request,
    lead_id: str | None = Form(None),
    contact_id: str | None = Form(None),
    tax_rate_id: str | None = Form(None),
    status: str | None = Form(None),
    project_type: str | None = Form(None),
    currency: str | None = Form(None),
    subtotal: str | None = Form(None),
    tax_total: str | None = Form(None),
    total: str | None = Form(None),
    expires_at: str | None = Form(None),
    notes: str | None = Form(None),
    is_active: str | None = Form(None),
    item_description: list[str] = Form([]),
    item_quantity: list[str] = Form([]),
    item_unit_price: list[str] = Form([]),
    item_inventory_item_id: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    form_input = QuoteUpsertInput(
        lead_id=lead_id,
        contact_id=contact_id,
        tax_rate_id=tax_rate_id,
        status=status,
        project_type=project_type,
        currency=currency,
        subtotal=subtotal,
        tax_total=tax_total,
        total=total,
        expires_at=expires_at,
        notes=notes,
        is_active=is_active,
        item_description=item_description,
        item_quantity=item_quantity,
        item_unit_price=item_unit_price,
        item_inventory_item_id=item_inventory_item_id,
    )
    try:
        create_quote(db, form=form_input, tax_rate_get=_billing_service().tax_rates.get)
        return RedirectResponse(url="/admin/crm/quotes", status_code=303)
    except (ValidationError, ValueError) as exc:
        error = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)

    options = _load_crm_sales_options(db)
    tax_rates = _billing_service().tax_rates.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    context = _crm_base_context(request, db, "quotes")
    context.update(
        quote_form_error_data(
            db,
            form=form_input,
            mode="create",
            quote_id=None,
            contacts=options["contacts"],
            tax_rates=tax_rates,
        )
    )
    context["error"] = error
    return templates.TemplateResponse("admin/crm/quote_form.html", context, status_code=400)


@router.get("/quotes/{quote_id}/edit", response_class=HTMLResponse)
def crm_quote_edit(request: Request, quote_id: str, db: Session = Depends(get_db)):
    options = _load_crm_sales_options(db)
    context = _crm_base_context(request, db, "quotes")
    context.update(edit_quote_form_data(db, quote_id=quote_id, contacts=options["contacts"]))
    context.update(
        {"form_title": "Edit Quote", "submit_label": "Save Quote", "action_url": f"/admin/crm/quotes/{quote_id}/edit"}
    )
    return templates.TemplateResponse("admin/crm/quote_form.html", context)


@router.post("/quotes/{quote_id}/edit", response_class=HTMLResponse)
def crm_quote_update(
    request: Request,
    quote_id: str,
    lead_id: str | None = Form(None),
    contact_id: str | None = Form(None),
    status: str | None = Form(None),
    project_type: str | None = Form(None),
    currency: str | None = Form(None),
    subtotal: str | None = Form(None),
    tax_total: str | None = Form(None),
    total: str | None = Form(None),
    expires_at: str | None = Form(None),
    notes: str | None = Form(None),
    is_active: str | None = Form(None),
    item_description: list[str] = Form([]),
    item_quantity: list[str] = Form([]),
    item_unit_price: list[str] = Form([]),
    item_inventory_item_id: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    form_input = QuoteUpsertInput(
        lead_id=lead_id,
        contact_id=contact_id,
        status=status,
        project_type=project_type,
        currency=currency,
        subtotal=subtotal,
        tax_total=tax_total,
        total=total,
        expires_at=expires_at,
        notes=notes,
        is_active=is_active,
        item_description=item_description,
        item_quantity=item_quantity,
        item_unit_price=item_unit_price,
        item_inventory_item_id=item_inventory_item_id,
    )
    try:
        before, updated = update_quote(db, quote_id=quote_id, form=form_input)
        metadata_payload = build_changes_metadata(before, updated)
        from app.web.admin import get_current_user

        current_user = get_current_user(request)
        log_audit_event(
            db=db,
            request=request,
            action="update",
            entity_type="quote",
            entity_id=str(quote_id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata=metadata_payload,
        )
        return RedirectResponse(url="/admin/crm/quotes", status_code=303)
    except (ValidationError, ValueError) as exc:
        db.rollback()
        error = str(exc)
    except Exception as exc:
        db.rollback()
        error = exc.detail if hasattr(exc, "detail") else str(exc)

    options = _load_crm_sales_options(db)
    tax_rates = _billing_service().tax_rates.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    context = _crm_base_context(request, db, "quotes")
    context.update(
        quote_form_error_data(
            db,
            form=form_input,
            mode="update",
            quote_id=quote_id,
            contacts=options["contacts"],
            tax_rates=tax_rates,
        )
    )
    context["error"] = error
    return templates.TemplateResponse("admin/crm/quote_form.html", context, status_code=400)


@router.post("/quotes/{quote_id}/delete", response_class=HTMLResponse)
def crm_quote_delete(request: Request, quote_id: str, db: Session = Depends(get_db)):
    _require_admin_role(request)
    delete_quote(db, quote_id)
    return RedirectResponse(url="/admin/crm/quotes", status_code=303)


@router.post("/quotes/bulk/status")
async def crm_quotes_bulk_status(
    request: Request,
    db: Session = Depends(get_db),
):
    """Bulk update quote status."""

    _require_admin_role(request)
    status_code, payload = bulk_status(db, await request.body())
    return JSONResponse(payload, status_code=status_code)


@router.post("/quotes/bulk/delete")
async def crm_quotes_bulk_delete(
    request: Request,
    db: Session = Depends(get_db),
):
    """Bulk delete quotes."""

    _require_admin_role(request)
    status_code, payload = bulk_delete(db, await request.body())
    return JSONResponse(payload, status_code=status_code)
