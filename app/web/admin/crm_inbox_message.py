"""CRM inbox message send route."""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox.formatting import (
    filter_messages_for_user,
    format_conversation_for_template,
    format_message_for_template,
)

router = APIRouter(tags=["web-admin-crm"])
templates = Jinja2Templates(directory="templates")


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


@router.post("/inbox/conversation/{conversation_id}/message", response_class=HTMLResponse)
async def send_message(
    request: Request,
    conversation_id: str,
    message: str | None = Form(None),
    attachments: str | None = Form(None),
    mentions: str | None = Form(None),
    idempotency_key: str | None = Form(None),
    reply_to_message_id: str | None = Form(None),
    template_id: str | None = Form(None),
    scheduled_at: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Send a message in a conversation."""
    from app.services.crm.inbox.admin_ui import send_conversation_message
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    trace_id = str(uuid.uuid4())

    author_id = current_user.get("person_id") if current_user.get("person_id") else None
    result = send_conversation_message(
        db=db,
        conversation_id=conversation_id,
        message_text=message,
        attachments_json=attachments,
        idempotency_key=idempotency_key,
        reply_to_message_id=reply_to_message_id,
        template_id=template_id,
        scheduled_at=scheduled_at,
        author_id=author_id,
        trace_id=trace_id,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )

    if result.kind == "forbidden":
        return HTMLResponse(
            "<div class='p-4 text-sm text-red-500'>Forbidden</div>",
            status_code=403,
        )
    if result.kind == "not_found":
        return HTMLResponse("<div class='p-8 text-center text-red-500'>Conversation not found</div>")
    if result.kind == "validation_error":
        detail = result.error_detail or "Message or attachment is required."
        return HTMLResponse(
            f"<div class='p-4 text-sm text-red-500'>{detail}</div>",
            status_code=422,
        )
    if result.kind == "send_failed":
        detail = quote(result.error_detail or "Meta rejected the outbound message.", safe="")
        url = f"/admin/crm/inbox?conversation_id={conversation_id}&reply_error=1&reply_error_detail={detail}"
        if request.headers.get("HX-Request") == "true":
            return Response(status_code=204, headers={"HX-Redirect": url})
        return RedirectResponse(url=url, status_code=303)

    # Mentions are optional and should never block sending a message.
    if mentions and result.message and result.conversation:
        try:
            import json

            from app.services.crm.inbox.notifications import notify_agents_mentioned

            parsed = json.loads(mentions)
            mentioned_agent_ids = parsed if isinstance(parsed, list) else []

            metadata = result.message.metadata_ if isinstance(result.message.metadata_, dict) else {}
            metadata["mentions"] = {"agent_ids": list(mentioned_agent_ids)}
            result.message.metadata_ = dict(metadata)
            db.add(result.message)
            db.commit()

            notify_agents_mentioned(
                db,
                conversation=result.conversation,
                message=result.message,
                mentioned_agent_ids=list(mentioned_agent_ids),
                actor_person_id=author_id,
            )
        except Exception:
            pass

    try:
        if not result.conversation:
            raise ValueError("Conversation not found")
        conversation = format_conversation_for_template(result.conversation, db, include_inbox_label=True)
        # Fetch latest 100 messages then reverse for chronological display.
        messages_raw = conversation_service.Messages.list(
            db=db,
            conversation_id=conversation_id,
            channel_type=None,
            direction=None,
            status=None,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        messages_raw = list(reversed(messages_raw))
        messages = [format_message_for_template(m, db) for m in messages_raw]
        current_roles = _get_current_roles(request)
        messages = filter_messages_for_user(
            messages,
            author_id,
            current_roles,
        )
        from app.logic import private_note_logic
        from app.services.crm.inbox.agents import list_active_agents_for_mentions
        from app.services.crm.inbox.templates import message_templates

        template_list = message_templates.list(
            db,
            channel_type=None,
            is_active=True,
            limit=200,
            offset=0,
        )
        mention_agents = list_active_agents_for_mentions(db)

        if request.headers.get("HX-Request") != "true":
            return RedirectResponse(
                url=f"/admin/crm/inbox?conversation_id={conversation_id}",
                status_code=303,
            )

        return templates.TemplateResponse(
            "admin/crm/_message_thread.html",
            {
                "request": request,
                "conversation": conversation,
                "messages": messages,
                "current_user": current_user,
                "current_roles": current_roles,
                "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
                "message_templates": template_list,
                "mention_agents": mention_agents,
            },
        )
    except Exception as exc:
        detail = quote(str(exc) or "Reply failed", safe="")
        return RedirectResponse(
            url=f"/admin/crm/inbox?conversation_id={conversation_id}&reply_error=1&reply_error_detail={detail}",
            status_code=303,
        )
