"""CRM quote routes."""

import os
import re
import tempfile
import uuid
from datetime import UTC, datetime
from html import escape as html_escape

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.logging import get_logger
from app.models.crm.enums import ChannelType, MessageStatus
from app.models.domain_settings import SettingDomain
from app.models.person import ChannelType as PersonChannelType
from app.models.person import Person
from app.schemas.crm.conversation import ConversationCreate
from app.schemas.crm.inbox import InboxSendRequest
from app.services import crm as crm_service
from app.services.audit_helpers import build_changes_metadata, log_audit_event
from app.services.crm import contact as contact_service
from app.services.crm import conversation as conversation_service
from app.services.crm import inbox as inbox_service
from app.services.crm.web_quotes import (
    QuoteUpsertInput,
    bulk_delete,
    bulk_status,
    create_quote,
    delete_quote,
    edit_quote_form_data,
    list_quotes_page_data,
    new_quote_form_data,
    quote_form_error_data,
    update_quote,
    update_quote_status,
)
from app.services.settings_spec import resolve_value

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
    quote = crm_service.quotes.get(db=db, quote_id=quote_id)
    items = crm_service.quote_line_items.list(
        db=db,
        quote_id=quote_id,
        order_by="created_at",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    lead = None
    if quote.lead_id:
        try:
            lead = crm_service.leads.get(db=db, lead_id=str(quote.lead_id))
        except Exception:
            lead = None
    contact = None
    if quote.person_id:
        contact = contact_service.get_person_with_relationships(db, str(quote.person_id))

    has_email = False
    has_whatsapp = False
    if contact and contact.channels:
        for channel in contact.channels:
            if not channel.address:
                continue
            if channel.channel_type == PersonChannelType.email:
                has_email = True
            if channel.channel_type == PersonChannelType.whatsapp:
                has_whatsapp = True

    company_name_raw = resolve_value(db, SettingDomain.comms, "company_name")
    company_name = (
        company_name_raw.strip() if isinstance(company_name_raw, str) and company_name_raw.strip() else "Dotmac"
    )
    support_email_raw = resolve_value(db, SettingDomain.comms, "support_email")
    support_email = (
        support_email_raw.strip() if isinstance(support_email_raw, str) and support_email_raw.strip() else None
    )
    summary_text = _format_project_summary(quote, lead, contact, company_name, support_email=support_email)

    context = _crm_base_context(request, db, "quotes")
    context.update(
        {
            "quote": quote,
            "items": items,
            "lead": lead,
            "contact": contact,
            "summary_text": summary_text,
            "has_email": has_email,
            "has_whatsapp": has_whatsapp,
            "today": datetime.now(UTC),
        }
    )
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

    quote = crm_service.quotes.get(db=db, quote_id=quote_id)
    items = crm_service.quote_line_items.list(
        db=db,
        quote_id=quote_id,
        order_by="created_at",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    lead = None
    if quote.lead_id:
        try:
            lead = crm_service.leads.get(db=db, lead_id=str(quote.lead_id))
        except Exception:
            lead = None

    if not quote.person_id:
        return RedirectResponse(
            url=f"/admin/crm/quotes/{quote_id}?send_error=1",
            status_code=303,
        )

    contact = contact_service.get_person_with_relationships(db, str(quote.person_id))
    if not contact:
        return RedirectResponse(
            url=f"/admin/crm/quotes/{quote_id}?send_error=1",
            status_code=303,
        )

    try:
        channel_enum = ChannelType(channel_type)
    except ValueError:
        return RedirectResponse(
            url=f"/admin/crm/quotes/{quote_id}?send_error=1",
            status_code=303,
        )

    if channel_enum not in (ChannelType.email, ChannelType.whatsapp):
        return RedirectResponse(
            url=f"/admin/crm/quotes/{quote_id}?send_error=1",
            status_code=303,
        )

    person_channel = conversation_service.resolve_person_channel(db, str(contact.id), channel_enum)
    if not person_channel:
        return RedirectResponse(
            url=f"/admin/crm/quotes/{quote_id}?send_error=1",
            status_code=303,
        )

    company_name_raw = resolve_value(db, SettingDomain.comms, "company_name")
    company_name = (
        company_name_raw.strip() if isinstance(company_name_raw, str) and company_name_raw.strip() else "Dotmac"
    )
    support_email_raw = resolve_value(db, SettingDomain.comms, "support_email")
    _support_email = (
        support_email_raw.strip() if isinstance(support_email_raw, str) and support_email_raw.strip() else None
    )
    body = (message or "").strip()
    if not body:
        body = _format_project_summary(quote, lead, contact, company_name, support_email=_support_email)

    quote_label = None
    if isinstance(quote.metadata_, dict):
        quote_label = quote.metadata_.get("quote_name")
    subject = None
    attachments_payload: list[dict] | None = None
    if channel_enum == ChannelType.email:
        subject = "Installation Quote"
        stored_name = None
        try:
            branding_payload = dict(getattr(request.state, "branding", None) or {})
            if "quote_banking_details" not in branding_payload:
                branding_payload["quote_banking_details"] = resolve_value(
                    db, SettingDomain.comms, "quote_banking_details"
                )
            pdf_bytes = _build_quote_pdf_bytes(
                request=request,
                quote=quote,
                items=items,
                lead=lead,
                contact=contact,
                quote_name=quote_label,
                branding=branding_payload,
            )
            max_size = settings.message_attachment_max_size_bytes
            if max_size and len(pdf_bytes) > max_size:
                return RedirectResponse(
                    url=f"/admin/crm/quotes/{quote_id}?send_error=1",
                    status_code=303,
                )
            stored_name = f"{uuid.uuid4().hex}.pdf"
            from app.services.crm.conversations import message_attachments as message_attachment_service

            saved = message_attachment_service.save(
                [
                    {
                        "stored_name": stored_name,
                        "file_name": f"quote_{quote.id}.pdf",
                        "file_size": len(pdf_bytes),
                        "mime_type": "application/pdf",
                        "content": pdf_bytes,
                    }
                ]
            )
            attachments_payload = saved or None
        except Exception:
            return RedirectResponse(
                url=f"/admin/crm/quotes/{quote_id}?send_error=1",
                status_code=303,
            )

    conversation = conversation_service.resolve_open_conversation_for_channel(db, str(contact.id), channel_enum)
    if not conversation:
        conversation = conversation_service.Conversations.create(
            db,
            ConversationCreate(
                person_id=contact.id,
                subject=subject if channel_enum == ChannelType.email else None,
            ),
        )

    current_user = get_current_user(request)
    author_id = current_user.get("person_id") if current_user else None

    try:
        result_msg = inbox_service.send_message(
            db,
            InboxSendRequest(
                conversation_id=conversation.id,
                channel_type=channel_enum,
                subject=subject,
                body=body,
                attachments=attachments_payload,
            ),
            author_id=author_id,
        )
        if result_msg and result_msg.status == MessageStatus.failed:
            return RedirectResponse(
                url=f"/admin/crm/quotes/{quote_id}?send_error=1",
                status_code=303,
            )
    except Exception:
        return RedirectResponse(
            url=f"/admin/crm/quotes/{quote_id}?send_error=1",
            status_code=303,
        )

    if attachments_payload and result_msg:
        _apply_message_attachments(db, result_msg, attachments_payload)

    return RedirectResponse(
        url=f"/admin/crm/quotes/{quote_id}?send=1",
        status_code=303,
    )


@router.get("/quotes/{quote_id}/pdf")
def crm_quote_pdf(request: Request, quote_id: str, db: Session = Depends(get_db)):
    quote = crm_service.quotes.get(db=db, quote_id=quote_id)
    items = crm_service.quote_line_items.list(
        db=db,
        quote_id=quote_id,
        order_by="created_at",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    lead = None
    if quote.lead_id:
        try:
            lead = crm_service.leads.get(db=db, lead_id=str(quote.lead_id))
        except Exception:
            lead = None
    contact = None
    if quote.person_id:
        contact = db.get(Person, quote.person_id)

    stored_name = None
    if isinstance(quote.metadata_, dict):
        stored_name = quote.metadata_.get("quote_name")
    quote_name = stored_name or (contact.display_name if contact and contact.display_name else None)
    if not quote_name and contact:
        quote_name = contact.email

    template = templates.get_template("admin/crm/quote_pdf.html")
    template_path = getattr(template, "filename", None)
    if template_path:
        template_path = os.path.abspath(template_path)
    branding_payload = dict(getattr(request.state, "branding", None) or {})
    if "quote_banking_details" not in branding_payload:
        branding_payload["quote_banking_details"] = resolve_value(db, SettingDomain.comms, "quote_banking_details")
    branding_payload["logo_src"] = _resolve_brand_logo_src(branding_payload, request)
    html = template.render(
        {
            "request": request,
            "quote": quote,
            "items": items,
            "lead": lead,
            "contact": contact,
            "quote_name": quote_name or "",
            "branding": branding_payload,
        }
    )
    if request.query_params.get("smoke") == "1":
        html = f"""
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>PDF Smoke Test</title></head>
<body style="font-family: Arial, sans-serif; font-size: 16px; line-height: 1.6;">
  <p>PDF smoke test</p>
  <p>Quote ID: {quote.id}</p>
  <p>Line items: {len(items) if items else 0}</p>
  <p>Subtotal: {quote.subtotal or 0}</p>
  <p>Tax: {quote.tax_total or 0}</p>
  <p>Total: {quote.total or 0}</p>
  <p>End of test.</p>
</body>
</html>
"""
    if request.query_params.get("plain") == "1":
        currency = quote.currency or ""
        plain_rows = []
        if items:
            for item in items:
                desc = html_escape(str(getattr(item, "description", "") or ""))
                qty = getattr(item, "quantity", 0) or 0
                unit_price = getattr(item, "unit_price", 0) or 0
                amount = getattr(item, "amount", 0) or 0
                plain_rows.append(
                    f"<tr><td>{desc}</td><td style='text-align:right'>{qty}</td>"
                    f"<td style='text-align:right'>{unit_price}</td>"
                    f"<td style='text-align:right'>{amount}</td></tr>"
                )
        else:
            plain_rows.append("<tr><td colspan='4'>No line items found.</td></tr>")
        plain_html = f"""
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Quote {quote.id}</title></head>
<body>
  <h1>{html_escape(quote_name or f"Quote {str(quote.id)[:8]}")}</h1>
  <div>Quote ID: {quote.id}</div>
  <div>Status: {(quote.status.value if quote.status else "draft")}</div>
  <div>Currency: {html_escape(currency)}</div>
  <table border="1" cellpadding="6" cellspacing="0" width="100%%">
    <thead>
      <tr><th>Description</th><th>Qty</th><th>Unit Price</th><th>Amount</th></tr>
    </thead>
    <tbody>
      {"".join(plain_rows)}
    </tbody>
  </table>
  <div>Subtotal: {quote.subtotal or 0}</div>
  <div>Tax: {quote.tax_total or 0}</div>
  <div>Total: {quote.total or 0}</div>
</body>
</html>
"""
        html = plain_html
    logger.info(
        "quote_pdf_template=%s quote_pdf_html_len=%s items=%s totals=subtotal:%s tax:%s total:%s currency=%s",
        template_path or "unknown",
        len(html),
        len(items) if items else 0,
        quote.subtotal,
        quote.tax_total,
        quote.total,
        quote.currency,
    )
    if request.query_params.get("debug") == "1":
        return HTMLResponse(content=html)
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="WeasyPrint is not installed on the server. Install it to generate PDFs.",
        ) from exc
    _ensure_pydyf_compat()
    if request.query_params.get("nocss") == "1":
        html = re.sub(r"<style[\\s\\S]*?</style>", "", html, flags=re.IGNORECASE)
    html_doc = HTML(string=html, base_url=str(request.base_url))
    pdf_bytes = html_doc.write_pdf()
    logger.info(
        "quote_pdf_len=%s",
        len(pdf_bytes),
    )
    if request.query_params.get("save") == "1":
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                delete=False,
                suffix=".html",
                prefix=f"quote_{quote.id}_",
            ) as html_handle:
                html_handle.write(html)
                tmp_html = html_handle.name
            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                suffix=".pdf",
                prefix=f"quote_{quote.id}_",
            ) as pdf_handle:
                pdf_handle.write(pdf_bytes)
                tmp_pdf = pdf_handle.name
            logger.info("quote_pdf_saved html=%s pdf=%s", tmp_html, tmp_pdf)
        except Exception:
            logger.exception("quote_pdf_save_failed")
    filename = f"quote_{quote.id}.pdf"
    disposition = "inline" if request.query_params.get("inline") == "1" else "attachment"
    return Response(
        pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


@router.get("/quotes/{quote_id}/preview", response_class=HTMLResponse)
def crm_quote_preview(request: Request, quote_id: str):
    extra = "&plain=1" if request.query_params.get("plain") == "1" else ""
    cache_bust = int(datetime.now(UTC).timestamp())
    pdf_url = f"/admin/crm/quotes/{quote_id}/pdf?inline=1{extra}&ts={cache_bust}"
    html = f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Quote PDF Preview</title>
  <style>
    html, body {{ height: 100%; margin: 0; }}
    .frame {{ width: 100%; height: 100vh; border: none; }}
  </style>
</head>
<body>
  <iframe class="frame" src="{pdf_url}" title="Quote PDF Preview"></iframe>
</body>
</html>
"""
    return HTMLResponse(content=html)


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
