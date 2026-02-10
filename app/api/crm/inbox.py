from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.crm.inbox import (
    EmailConnectorCreate,
    EmailPollingJobRequest,
    EmailWebhookPayload,
    InboxSendRequest,
    InboxSendResponse,
    WhatsAppWebhookPayload,
)
from app.schemas.crm.message_template import (
    MessageTemplateCreate,
    MessageTemplateRead,
    MessageTemplateUpdate,
)
from app.schemas.integration import IntegrationJobRead, IntegrationTargetRead
from app.services import crm as crm_service
from app.services.crm.inbox.errors import InboxError
from app.services.crm.inbox.outbox import enqueue_outbound_message
from app.services.crm.inbox.templates import message_templates

router = APIRouter(prefix="/crm/inbox", tags=["crm-inbox"])


class InboxSendAsyncResponse(BaseModel):
    outbox_id: str
    status: str


@router.post("/send", response_model=InboxSendResponse, status_code=status.HTTP_201_CREATED)
def send_message(payload: InboxSendRequest, db: Session = Depends(get_db)):
    try:
        if payload.scheduled_at and payload.scheduled_at > datetime.now(UTC):
            raise HTTPException(status_code=400, detail="Use send-async for scheduled messages")
        message = crm_service.inbox.send_message(db, payload)
        return InboxSendResponse(message_id=message.id, status=message.status.value)
    except InboxError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post(
    "/send-async",
    response_model=InboxSendAsyncResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def send_message_async(
    payload: InboxSendRequest,
    db: Session = Depends(get_db),
    idempotency_key: str | None = None,
):
    outbox = enqueue_outbound_message(
        db,
        payload=payload,
        author_id=None,
        idempotency_key=idempotency_key,
        scheduled_at=payload.scheduled_at,
        dispatch=True,
    )
    return InboxSendAsyncResponse(outbox_id=str(outbox.id), status=outbox.status)


@router.get("/templates", response_model=ListResponse[MessageTemplateRead])
def list_templates(
    db: Session = Depends(get_db),
    channel_type: str | None = None,
    is_active: bool | None = None,
    limit: int = 100,
    offset: int = 0,
):
    return message_templates.list_response(
        db,
        channel_type=channel_type,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )


@router.post("/templates", response_model=MessageTemplateRead, status_code=status.HTTP_201_CREATED)
def create_template(payload: MessageTemplateCreate, db: Session = Depends(get_db)):
    return message_templates.create(db, payload)


@router.get("/templates/{template_id}", response_model=MessageTemplateRead)
def get_template(template_id: str, db: Session = Depends(get_db)):
    return message_templates.get(db, template_id)


@router.patch("/templates/{template_id}", response_model=MessageTemplateRead)
def update_template(template_id: str, payload: MessageTemplateUpdate, db: Session = Depends(get_db)):
    return message_templates.update(db, template_id, payload)


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: str, db: Session = Depends(get_db)):
    message_templates.delete(db, template_id)


@router.post(
    "/email-connector",
    response_model=IntegrationTargetRead,
    status_code=status.HTTP_201_CREATED,
)
def create_email_connector(payload: EmailConnectorCreate, db: Session = Depends(get_db)):
    return crm_service.inbox.create_email_connector_target(
        db,
        name=payload.name,
        smtp=payload.smtp,
        imap=payload.imap,
        pop3=payload.pop3,
        auth_config=payload.auth_config,
    )


@router.post(
    "/email-polling-job",
    response_model=IntegrationJobRead,
    status_code=status.HTTP_201_CREATED,
)
def create_email_polling_job(
    payload: EmailPollingJobRequest, db: Session = Depends(get_db)
):
    interval_seconds = payload.interval_seconds
    if payload.interval_minutes is not None:
        interval_seconds = max(payload.interval_minutes, 1) * 60
    return crm_service.inbox.ensure_email_polling_job(
        db,
        target_id=str(payload.target_id),
        interval_seconds=interval_seconds,
        name=payload.name,
    )


@router.post("/webhooks/whatsapp", status_code=status.HTTP_200_OK)
def whatsapp_webhook(payload: WhatsAppWebhookPayload, db: Session = Depends(get_db)):
    crm_service.inbox.receive_whatsapp_message(db, payload)
    return {"status": "ok"}


@router.post("/webhooks/email", status_code=status.HTTP_200_OK)
def email_webhook(payload: EmailWebhookPayload, db: Session = Depends(get_db)):
    crm_service.inbox.receive_email_message(db, payload)
    return {"status": "ok"}
