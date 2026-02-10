import json
import time
from datetime import UTC, datetime
from typing import TypedDict

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from app.db import SessionLocal
from app.logging import get_logger
from app.schemas.crm.inbox import EmailWebhookPayload, MetaWebhookPayload, WhatsAppWebhookPayload
from app.services import meta_oauth, meta_webhooks
from app.tasks import webhooks as webhook_tasks

logger = get_logger(__name__)

router = APIRouter(prefix="/webhooks/crm", tags=["web-public-crm"])

class VerifyTokenCache(TypedDict):
    value: str | None
    loaded_at: float


_VERIFY_TOKEN_CACHE: dict[str, VerifyTokenCache] = {
    "meta": {"value": None, "loaded_at": 0.0},
    "whatsapp": {"value": None, "loaded_at": 0.0},
}
_VERIFY_TOKEN_TTL_SECONDS = 300.0


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def _get_verify_token(db: Session, channel: str = "meta") -> str | None:
    cache_key = "whatsapp" if channel == "whatsapp" else "meta"
    cache = _VERIFY_TOKEN_CACHE.get(cache_key)
    if cache is None:
        cache = {"value": None, "loaded_at": 0.0}
        _VERIFY_TOKEN_CACHE[cache_key] = cache
    now = time.monotonic()
    loaded_at = float(cache.get("loaded_at", 0.0))
    if now - loaded_at > _VERIFY_TOKEN_TTL_SECONDS:
        settings = meta_oauth.get_meta_settings(db)
        if cache_key == "whatsapp":
            cache["value"] = (
                settings.get("whatsapp_webhook_verify_token")
                or settings.get("meta_webhook_verify_token")
            )
        else:
            cache["value"] = settings.get("meta_webhook_verify_token")
        cache["loaded_at"] = now
        _VERIFY_TOKEN_CACHE[cache_key] = cache
    return cache.get("value")

def _extract_meta_whatsapp_messages(payload: dict) -> list[WhatsAppWebhookPayload]:
    messages: list[WhatsAppWebhookPayload] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            raw_messages = value.get("messages") or []
            contacts = value.get("contacts") or []
            contact = contacts[0] if contacts else {}
            profile = contact.get("profile") or {}
            contact_name = profile.get("name")
            metadata = value.get("metadata") or {}
            phone_number_id = metadata.get("phone_number_id")
            display_phone_number = metadata.get("display_phone_number")

            for msg in raw_messages:
                contact_address = msg.get("from")
                msg_type = msg.get("type")
                body = None
                if msg_type == "text":
                    body = (msg.get("text") or {}).get("body")
                if not body:
                    body = f"[{msg_type} message]" if msg_type else "[whatsapp message]"

                received_at = None
                timestamp = msg.get("timestamp")
                if timestamp:
                    try:
                        received_at = datetime.fromtimestamp(int(timestamp), tz=UTC)
                    except Exception:
                        received_at = None

                attachments = []
                if msg_type in {"image", "video", "audio", "document", "sticker"}:
                    media = msg.get(msg_type)
                    if isinstance(media, dict):
                        media_id = media.get("id")
                        if media_id:
                            attachments.append(
                                {
                                    "type": msg_type,
                                    "file_name": media.get("filename"),
                                    "mime_type": media.get("mime_type"),
                                    "payload": {
                                        "id": media_id,
                                        "mime_type": media.get("mime_type"),
                                        "sha256": media.get("sha256"),
                                        "caption": media.get("caption"),
                                        "filename": media.get("filename"),
                                    },
                                }
                            )

                location_payload = None
                if msg_type == "location":
                    location = msg.get("location")
                    if isinstance(location, dict):
                        location_label = (
                            location.get("name")
                            or location.get("address")
                            or location.get("label")
                        )
                        lat = location.get("latitude")
                        lng = location.get("longitude")
                        maps_link = None
                        if lat is not None and lng is not None:
                            maps_link = f"https://maps.google.com/?q={lat},{lng}"
                        location_payload = {
                            "type": "location",
                            "latitude": location.get("latitude"),
                            "longitude": location.get("longitude"),
                            "address": location.get("address"),
                            "name": location.get("name"),
                            "label": location_label,
                            "location": location,
                        }
                        if not body or body.startswith("[location"):
                            body = f"📍 {location_label}" if location_label else (
                                f"📍 {maps_link}" if maps_link else "📍 Location shared"
                            )

                reaction_payload = None
                if msg_type == "reaction":
                    reaction = msg.get("reaction")
                    if isinstance(reaction, dict):
                        emoji = reaction.get("emoji")
                        reaction_payload = {
                            "type": "reaction",
                            "emoji": emoji,
                        }
                        if not body or body.startswith("[reaction"):
                            if isinstance(emoji, str) and emoji.strip():
                                body = f"Reaction {emoji.strip()}"
                            else:
                                body = "Reaction received"

                metadata_payload = {
                    "phone_number_id": phone_number_id,
                    "display_phone_number": display_phone_number,
                    "attachments": attachments or None,
                    "raw": value,
                }
                context = msg.get("context")
                if isinstance(context, dict):
                    context_id = context.get("id")
                    if context_id:
                        metadata_payload["context_message_id"] = context_id
                if location_payload:
                    metadata_payload.update(location_payload)
                if reaction_payload:
                    metadata_payload.update(reaction_payload)

                try:
                    messages.append(
                        WhatsAppWebhookPayload(
                            contact_address=contact_address or "",
                            contact_name=contact_name,
                            message_id=msg.get("id"),
                            body=body,
                            received_at=received_at,
                            metadata=metadata_payload,
                        )
                    )
                except Exception:
                    continue

    return messages

@router.get("/whatsapp")
async def whatsapp_webhook_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    db: Session = Depends(get_db),
):
    """Handle WhatsApp webhook verification challenge."""
    expected_token = _get_verify_token(db, channel="whatsapp")
    if not expected_token:
        logger.warning("whatsapp_webhook_verify_failed reason=no_verify_token_configured")
        return Response(status_code=403)
    if hub_mode == "subscribe" and hub_verify_token == expected_token:
        logger.info("whatsapp_webhook_verified")
        return Response(content=hub_challenge, media_type="text/plain")
    logger.warning(
        "whatsapp_webhook_verify_failed mode=%s token_match=%s",
        hub_mode,
        hub_verify_token == expected_token if expected_token else "N/A",
    )
    return Response(status_code=403)

@router.post("/whatsapp", status_code=status.HTTP_200_OK)
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle WhatsApp webhook events.

    Accepts either the internal normalized payload or Meta's native payload.
    """
    try:
        body = await request.body()
    except ClientDisconnect:
        logger.warning("whatsapp_webhook_client_disconnect")
        return Response(status_code=400)

    # Optional signature validation if header + app secret are available
    signature = request.headers.get("X-Hub-Signature-256")
    settings = meta_oauth.get_meta_settings(db)
    app_secret = settings.get("whatsapp_app_secret") or settings.get("meta_app_secret")
    if signature and app_secret:
        try:
            if not meta_webhooks.verify_webhook_signature(body, signature, app_secret):
                logger.warning("whatsapp_webhook_signature_invalid")
                return Response(status_code=401)
        except Exception as exc:
            logger.warning("whatsapp_webhook_signature_validation_failed error=%s", exc)
            return Response(status_code=401)

    # First try normalized payload
    try:
        parsed = WhatsAppWebhookPayload.model_validate_json(body)
        webhook_tasks.process_whatsapp_webhook.delay(parsed.model_dump())
        return {"status": "ok", "processed": 1}
    except Exception:
        pass

    # Fallback to Meta native payload
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        logger.warning("whatsapp_webhook_invalid_payload error=%s", exc)
        return {"status": "error", "detail": "Invalid payload"}

    messages = _extract_meta_whatsapp_messages(payload)
    if not messages:
        return {"status": "ok", "processed": 0}

    for msg in messages:
        webhook_tasks.process_whatsapp_webhook.delay(msg.model_dump())

    logger.info("whatsapp_webhook_enqueued events=%d", len(messages))
    return {"status": "ok", "processed": len(messages)}


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
    expected_token = _get_verify_token(db, "meta")

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
