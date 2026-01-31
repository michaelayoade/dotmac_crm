from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.crm.inbox import (
    EmailConnectorCreate,
    EmailPollingJobRequest,
    EmailWebhookPayload,
    InboxSendRequest,
    InboxSendResponse,
    WhatsAppWebhookPayload,
)
from app.schemas.integration import IntegrationTargetRead
from app.schemas.integration import IntegrationJobRead
from app.services import crm as crm_service

router = APIRouter(prefix="/crm/inbox", tags=["crm-inbox"])


@router.post("/send", response_model=InboxSendResponse, status_code=status.HTTP_201_CREATED)
def send_message(payload: InboxSendRequest, db: Session = Depends(get_db)):
    message = crm_service.inbox.send_message(db, payload)
    return InboxSendResponse(message_id=message.id, status=message.status.value)


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
