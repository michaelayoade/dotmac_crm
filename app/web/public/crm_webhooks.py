import json
import random
import time
import uuid
from datetime import UTC, datetime
from typing import TypedDict

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from app.db import SessionLocal
from app.logging import get_logger
from app.schemas.crm.inbox import EmailWebhookPayload, MetaWebhookPayload, WhatsAppWebhookPayload
from app.services import meta_oauth, meta_webhooks
from app.services.webhook_dead_letter import write_dead_letter
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
_WEBHOOK_SAMPLE_RATE = 0.02
_CHANNEL_STATS: dict[str, dict[str, float]] = {
    "meta": {"count": 0.0, "errors": 0.0, "last_log": 0.0},
    "whatsapp": {"count": 0.0, "errors": 0.0, "last_log": 0.0},
    "email": {"count": 0.0, "errors": 0.0, "last_log": 0.0},
}
_METRICS_LOG_INTERVAL_SECONDS = 60.0


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _should_sample() -> bool:
    return random.random() < _WEBHOOK_SAMPLE_RATE


def _record_channel_stat(channel: str, ok: bool, events: int | None = None) -> None:
    stats = _CHANNEL_STATS.get(channel)
    if not stats:
        return
    stats["count"] += float(events or 1)
    if not ok:
        stats["errors"] += 1.0
    now = time.monotonic()
    last_log = stats.get("last_log", 0.0)
    if now - last_log >= _METRICS_LOG_INTERVAL_SECONDS:
        error_rate = 0.0
        if stats["count"]:
            error_rate = stats["errors"] / stats["count"]
        logger.info(
            "webhook_channel_metrics channel=%s count=%s errors=%s error_rate=%.3f",
            channel,
            int(stats["count"]),
            int(stats["errors"]),
            error_rate,
        )
        stats["count"] = 0.0
        stats["errors"] = 0.0
        stats["last_log"] = now


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
            cache["value"] = settings.get("whatsapp_webhook_verify_token") or settings.get("meta_webhook_verify_token")
        else:
            cache["value"] = settings.get("meta_webhook_verify_token")
        cache["loaded_at"] = now
        _VERIFY_TOKEN_CACHE[cache_key] = cache
    return cache.get("value")


def _extract_meta_whatsapp_messages(payload: dict, trace_id: str | None = None) -> list[WhatsAppWebhookPayload]:
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
                        location_label = location.get("name") or location.get("address") or location.get("label")
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
                            body = (
                                f"📍 {location_label}"
                                if location_label
                                else (f"📍 {maps_link}" if maps_link else "📍 Location shared")
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

                message_payload: WhatsAppWebhookPayload | None = None
                try:
                    message_payload = WhatsAppWebhookPayload(
                        contact_address=contact_address or "",
                        contact_name=contact_name,
                        message_id=msg.get("id"),
                        body=body,
                        received_at=received_at,
                        metadata=metadata_payload,
                    )
                except Exception as parse_exc:
                    logger.warning(
                        "whatsapp_webhook_message_parse_failed trace_id=%s msg_id=%s",
                        trace_id,
                        msg.get("id", "unknown"),
                        exc_info=True,
                    )
                    write_dead_letter(
                        channel="whatsapp",
                        raw_payload=msg,
                        error=parse_exc,
                        trace_id=trace_id,
                        message_id=msg.get("id"),
                    )
                if message_payload:
                    messages.append(message_payload)

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
    trace_id = str(uuid.uuid4())
    start_time = time.monotonic()
    if _should_sample():
        logger.info("webhook_received channel=whatsapp trace_id=%s", trace_id)
    try:
        body = await request.body()
    except ClientDisconnect:
        logger.warning("whatsapp_webhook_client_disconnect trace_id=%s", trace_id)
        _record_channel_stat("whatsapp", ok=False)
        # Return 500 so the provider retries — body was not fully read.
        return Response(status_code=500)

    try:
        # First try normalized payload
        try:
            parsed = WhatsAppWebhookPayload.model_validate_json(body)
            webhook_tasks.process_whatsapp_webhook.delay(parsed.model_dump(), trace_id=trace_id)
            _record_channel_stat("whatsapp", ok=True)
            if _should_sample():
                body_len = len(parsed.body) if parsed.body else 0
                attachments = len(parsed.metadata.get("attachments", [])) if isinstance(parsed.metadata, dict) else 0
                logger.info(
                    "webhook_parsed channel=whatsapp trace_id=%s body_len=%s attachments=%s latency_ms=%s",
                    trace_id,
                    body_len,
                    attachments,
                    int((time.monotonic() - start_time) * 1000),
                )
            return {"status": "ok", "processed": 1}
        except Exception:
            logger.debug("Failed to parse normalized WhatsApp webhook body.", exc_info=True)

        # Fallback to Meta native payload; require valid signature.
        settings = meta_oauth.get_meta_settings(db)
        app_secret = settings.get("whatsapp_app_secret") or settings.get("meta_app_secret")
        signature = request.headers.get("X-Hub-Signature-256")
        if not app_secret:
            logger.warning("whatsapp_webhook_secret_missing")
            _record_channel_stat("whatsapp", ok=False)
            return JSONResponse(
                status_code=503,
                content={"status": "error", "detail": "Webhook secret not configured"},
            )
        if not signature:
            logger.warning("whatsapp_webhook_signature_missing")
            _record_channel_stat("whatsapp", ok=False)
            return JSONResponse(
                status_code=401,
                content={"status": "error", "detail": "Signature required"},
            )
        try:
            if not meta_webhooks.verify_webhook_signature(body, signature, app_secret):
                logger.warning("whatsapp_webhook_signature_invalid")
                _record_channel_stat("whatsapp", ok=False)
                return JSONResponse(
                    status_code=401,
                    content={"status": "error", "detail": "Invalid signature"},
                )
        except Exception as exc:
            logger.warning("whatsapp_webhook_signature_validation_failed error=%s", exc)
            _record_channel_stat("whatsapp", ok=False)
            return JSONResponse(
                status_code=401,
                content={"status": "error", "detail": "Signature validation failed"},
            )

        # Fallback to Meta native payload
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception as exc:
            logger.warning("whatsapp_webhook_invalid_payload error=%s", exc)
            _record_channel_stat("whatsapp", ok=False)
            return JSONResponse(
                status_code=400,
                content={"status": "error", "detail": "Invalid payload"},
            )

        messages = _extract_meta_whatsapp_messages(payload, trace_id=trace_id)
        if not messages:
            _record_channel_stat("whatsapp", ok=True, events=0)
            return {"status": "ok", "processed": 0}

        for msg in messages:
            webhook_tasks.process_whatsapp_webhook.delay(msg.model_dump(), trace_id=trace_id)

        logger.info("whatsapp_webhook_enqueued events=%d", len(messages))
        _record_channel_stat("whatsapp", ok=True, events=len(messages))
        if _should_sample():
            logger.info(
                "webhook_enqueued channel=whatsapp trace_id=%s events=%s latency_ms=%s",
                trace_id,
                len(messages),
                int((time.monotonic() - start_time) * 1000),
            )
        return {"status": "ok", "processed": len(messages)}
    except Exception:
        logger.exception("whatsapp_webhook_unhandled")
        _record_channel_stat("whatsapp", ok=False)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": "Unhandled error"},
        )


@router.post("/email", status_code=status.HTTP_200_OK)
def email_webhook(payload: EmailWebhookPayload, db: Session = Depends(get_db)):
    trace_id = str(uuid.uuid4())
    if _should_sample():
        body_len = len(payload.body) if payload.body else 0
        attachments = len(payload.metadata.get("attachments", [])) if isinstance(payload.metadata, dict) else 0
        logger.info(
            "webhook_received channel=email trace_id=%s body_len=%s attachments=%s",
            trace_id,
            body_len,
            attachments,
        )
    webhook_tasks.process_email_webhook.delay(payload.model_dump(), trace_id=trace_id)
    _record_channel_stat("email", ok=True)
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
    trace_id = str(uuid.uuid4())
    start_time = time.monotonic()
    if _should_sample():
        logger.info("webhook_received channel=meta trace_id=%s", trace_id)
    try:
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
            logger.warning("meta_webhook_client_disconnect trace_id=%s", trace_id)
            _record_channel_stat("meta", ok=False)
            # Return 500 so the provider retries — body was not fully read.
            return Response(status_code=500)

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
            _record_channel_stat("meta", ok=False)
            return JSONResponse(
                status_code=400,
                content={"status": "error", "detail": "Invalid payload"},
            )

        event_count = sum(len(entry.messaging or []) + len(entry.changes or []) for entry in payload.entry)
        webhook_tasks.process_meta_webhook.delay(payload.model_dump(), trace_id=trace_id)
        logger.info("meta_webhook_enqueued type=%s events=%d", payload.object, event_count)
        _record_channel_stat("meta", ok=True, events=event_count)
        if _should_sample():
            logger.info(
                "webhook_enqueued channel=meta trace_id=%s object=%s events=%s latency_ms=%s",
                trace_id,
                payload.object,
                event_count,
                int((time.monotonic() - start_time) * 1000),
            )
        return {"status": "ok", "processed": event_count}
    except Exception:
        logger.exception("meta_webhook_unhandled")
        _record_channel_stat("meta", ok=False)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": "Unhandled error"},
        )
