"""CRM inbox new-conversation route."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import SessionLocal

router = APIRouter(tags=["web-admin-crm"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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


@router.post("/inbox/conversation/new", response_class=HTMLResponse)
async def start_new_conversation(
    request: Request,
    channel_type: str = Form(...),
    channel_target_id: str | None = Form(None),
    contact_id: str | None = Form(None),
    contact_address: str = Form(...),
    contact_name: str | None = Form(None),
    cc_addresses: str | None = Form(None),
    subject: str | None = Form(None),
    message: str | None = Form(None),
    attachments: UploadFile | list[UploadFile] | tuple[UploadFile, ...] | None = File(None),
    whatsapp_template_name: str | None = Form(None),
    whatsapp_template_language: str | None = Form(None),
    whatsapp_template_components: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Start a new outbound conversation."""
    from app.services.crm.conversations import message_attachments as message_attachment_service
    from app.services.crm.inbox.admin_ui import start_new_conversation
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    attachments_payload: list[dict] | None = None
    try:
        prepared = await message_attachment_service.prepare(attachments)
        saved = message_attachment_service.save(prepared)
        attachments_payload = saved or None
    except Exception as exc:
        detail = quote(str(getattr(exc, "detail", None) or str(exc) or "Attachment upload failed"), safe="")
        return RedirectResponse(
            url=f"/admin/crm/inbox?new_error=1&new_error_detail={detail}",
            status_code=303,
        )

    result = start_new_conversation(
        db,
        channel_type=channel_type,
        channel_target_id=channel_target_id,
        contact_id=contact_id,
        contact_address=contact_address,
        contact_name=contact_name,
        cc_addresses_raw=cc_addresses,
        subject=subject,
        message_text=message,
        attachments_payload=attachments_payload,
        whatsapp_template_name=whatsapp_template_name,
        whatsapp_template_language=whatsapp_template_language,
        whatsapp_template_components=whatsapp_template_components,
        author_person_id=current_user.get("person_id") if current_user else None,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )

    if result.kind == "forbidden":
        return HTMLResponse(
            "<div class='p-6 text-center text-slate-500'>Forbidden</div>",
            status_code=403,
        )
    if result.kind != "success":
        detail = quote(result.error_detail or "Failed to send message", safe="")
        return RedirectResponse(
            url=f"/admin/crm/inbox?new_error=1&new_error_detail={detail}",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/admin/crm/inbox?conversation_id={result.conversation_id}",
        status_code=303,
    )
