import time

from fastapi import APIRouter, Depends, Query, Request, Response, status
from starlette.requests import ClientDisconnect
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.logging import get_logger
from app.schemas.crm.inbox import EmailWebhookPayload, MetaWebhookPayload, WhatsAppWebhookPayload
from app.services import meta_oauth
from app.services import meta_webhooks
from app.tasks import webhooks as webhook_tasks

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks/crm", tags=["web-public-crm"])

_VERIFY_TOKEN_CACHE = {"value": None, "loaded_at": 0.0}
_VERIFY_TOKEN_TTL_SECONDS = 300.0


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/whatsapp", status_code=status.HTTP_200_OK)
def whatsapp_webhook(payload: WhatsAppWebhookPayload, db: Session = Depends(get_db)):
    webhook_tasks.process_whatsapp_webhook.delay(payload.model_dump())
    return {"status": "ok"}


@router.post("/email", status_code=status.HTTP_200_OK)
def email_webhook(payload: EmailWebhookPayload, db: Session = Depends(get_db)):
    webhook_tasks.process_email_webhook.delay(payload.model_dump())
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Meta (Facebook/Instagram) Webhooks
# --------------------------------------------------------------------------


@router.get("/meta")
async def meta_webhook_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    db: Session = Depends(get_db),
):
    """Handle Meta webhook verification challenge.

    Meta sends a GET request to verify the webhook URL during setup.
    We verify the token matches our configured token and return the challenge.
    """
    # Get expected token from database settings with short-lived cache
    now = time.monotonic()
    loaded_at = float(_VERIFY_TOKEN_CACHE.get("loaded_at") or 0.0)
    if now - loaded_at > _VERIFY_TOKEN_TTL_SECONDS:
        settings = meta_oauth.get_meta_settings(db)
        _VERIFY_TOKEN_CACHE["value"] = settings.get("meta_webhook_verify_token")
        _VERIFY_TOKEN_CACHE["loaded_at"] = now
    expected_token = _VERIFY_TOKEN_CACHE["value"]

    if not expected_token:
        logger.warning("meta_webhook_verify_failed reason=no_verify_token_configured")
        return Response(status_code=403)

    if hub_mode == "subscribe" and hub_verify_token == expected_token:
        logger.info("meta_webhook_verified")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning(
        "meta_webhook_verify_failed mode=%s token_match=%s",
        hub_mode,
        hub_verify_token == expected_token if expected_token else "N/A",
    )
    return Response(status_code=403)


@router.post("/meta", status_code=status.HTTP_200_OK)
async def meta_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """Handle Meta (Facebook/Instagram) webhook events.

    Receives messaging events from Facebook Messenger and Instagram DMs.
    Verifies the X-Hub-Signature-256 header before processing.
    """
    # Get settings from database
    settings = meta_oauth.get_meta_settings(db)
    app_secret = settings.get("meta_app_secret")

    if not app_secret:
        logger.warning("meta_webhook_secret_missing")
        return Response(status_code=403)

    # Get signature and body for verification (read once, cache on request.state)
    signature = request.headers.get("X-Hub-Signature-256")
    try:
        body = getattr(request.state, "raw_body", None)
        if body is None:
            body = await request.body()
            request.state.raw_body = body
    except ClientDisconnect:
        logger.warning("meta_webhook_client_disconnect")
        return Response(status_code=400)

    try:
        signature_valid = meta_webhooks.verify_webhook_signature(body, signature, app_secret)
    except Exception as exc:
        logger.warning("meta_webhook_signature_validation_failed error=%s", exc)
        return Response(status_code=401)
    if not signature_valid:
        logger.warning("meta_webhook_signature_invalid")
        return Response(status_code=401)

    try:
        payload = MetaWebhookPayload.model_validate_json(body)
    except Exception as exc:
        logger.warning("meta_webhook_invalid_payload error=%s", exc)
        return {"status": "error", "detail": "Invalid payload"}

    event_count = sum(
        len(entry.messaging or []) + len(entry.changes or [])
        for entry in payload.entry
    )
    webhook_tasks.process_meta_webhook.delay(payload.model_dump())
    logger.info("meta_webhook_enqueued type=%s events=%d", payload.object, event_count)
    return {"status": "ok", "processed": event_count}
