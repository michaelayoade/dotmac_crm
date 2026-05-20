import hashlib
import hmac
import json
import random
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypedDict

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from app.config import settings
from app.db import SessionLocal
from app.logging import get_logger
from app.schemas.crm.inbox import EmailWebhookPayload, MetaWebhookPayload, WhatsAppWebhookPayload
from app.services import meta_oauth, meta_webhooks
from app.services.webhook_dead_letter import write_dead_letter

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
_WHATSAPP_REFERRAL_KEYS = {
    "source",
    "source_type",
    "source_id",
    "headline",
    "body",
    "media_type",
    "image_url",
    "video_url",
    "thumbnail_url",
    "ctwa_clid",
    "ad_id",
    "campaign_id",
    "source_url",
}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _should_sample() -> bool:
    return random.random() < _WEBHOOK_SAMPLE_RATE  # nosec B311 — sampling, not security


def _coerce_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _clean_whatsapp_referral_data(value: object) -> str | dict | None:
    if isinstance(value, str):
        candidate = value.strip()
        return candidate or None
    if not isinstance(value, dict):
        return None
    cleaned: dict[str, object] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            continue
        if isinstance(raw, str):
            candidate = raw.strip()
            if candidate:
                cleaned[key] = candidate
        elif isinstance(raw, (int, float, bool)):
            cleaned[key] = raw
    return cleaned or None


def _extract_whatsapp_attribution(message: dict) -> dict | None:
    if not isinstance(message, dict):
        return None
    referral = message.get("referral")
    referral_data = referral if isinstance(referral, dict) else {}
    if not referral_data:
        return None
    attribution = meta_webhooks._extract_meta_attribution(referral_data)
    source_url = _coerce_text(referral_data.get("source_url"))
    if source_url:
        attribution["source_url"] = source_url
    clean_referral_data = _clean_whatsapp_referral_data(referral_data.get("referral_data"))
    if clean_referral_data is not None:
        attribution["referral_data"] = clean_referral_data
    bounded_referral: dict[str, object] = {}
    for key in _WHATSAPP_REFERRAL_KEYS:
        raw = referral_data.get(key)
        if isinstance(raw, str):
            candidate = raw.strip()
            if candidate:
                bounded_referral[key] = candidate
        elif isinstance(raw, (int, float, bool)):
            bounded_referral[key] = raw
    if clean_referral_data is not None:
        bounded_referral["referral_data"] = clean_referral_data
    if bounded_referral:
        attribution["referral"] = bounded_referral
    return attribution or None


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


def _is_broker_error(exc: Exception) -> bool:
    """Check if an exception indicates the broker (Redis) is unreachable."""
    # kombu.exceptions.OperationalError covers connection refused, timeout, etc.
    cls_name = type(exc).__name__
    if cls_name in ("OperationalError", "ConnectionError", "TimeoutError"):
        return True
    # redis-py wraps connection errors in its own ConnectionError
    return "Connection" in cls_name or "Timeout" in cls_name


def _enqueue_webhook_task(
    delay_fn: Callable[..., object],
    *,
    channel: str,
    payload: dict,
    trace_id: str,
    message_id: str | None = None,
) -> tuple[bool, bool]:
    """Enqueue a webhook payload to Celery.

    Returns (enqueued, broker_error): enqueued is True on success,
    broker_error is True if the failure was a connection/timeout issue
    (vs a per-message serialization error).

    Relies on broker_connection_timeout (3-4s) configured in celery_app.py
    to fail fast when Redis is unreachable.
    """
    try:
        delay_fn(payload, trace_id=trace_id)
        return True, False
    except Exception as exc:
        logger.warning(
            "webhook_enqueue_failed channel=%s trace_id=%s message_id=%s error=%s",
            channel,
            trace_id,
            message_id or "",
            exc,
        )
        write_dead_letter(
            channel=channel,
            raw_payload=payload,
            error=exc,
            trace_id=trace_id,
            message_id=message_id,
        )
        return False, _is_broker_error(exc)


def _webhook_tasks():
    from app.tasks import webhooks as webhook_tasks

    return webhook_tasks


def _meta_signature_debug_enabled() -> bool:
    return settings.meta_webhook_debug


def _meta_signature_compare_debug_enabled() -> bool:
    return settings.meta_webhook_debug_signatures


def _raw_body_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _fingerprint_secret(secret: str | None) -> str | None:
    if not secret:
        return None
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


def _extract_meta_signature_debug_context(body: bytes, headers: dict[str, str]) -> dict[str, str | None]:
    payload_object: str | None = None
    entry_id: str | None = None
    page_id: str | None = None
    instagram_account_id: str | None = None
    payload_app_id: str | None = None
    header_app_id = headers.get("X-App-Id") or headers.get("X-Meta-App-Id") or headers.get("X-FB-App-Id")

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None

    if isinstance(payload, dict):
        payload_object = _coerce_text(payload.get("object"))
        payload_app_id = _coerce_text(payload.get("app_id"))
        entry = payload.get("entry")
        if isinstance(entry, list) and entry:
            first_entry = entry[0]
            if isinstance(first_entry, dict):
                entry_id = _coerce_text(first_entry.get("id"))
                page_id = _coerce_text(first_entry.get("id"))
                if payload_app_id is None:
                    payload_app_id = _coerce_text(first_entry.get("app_id"))

                messaging = first_entry.get("messaging")
                if isinstance(messaging, list) and messaging:
                    first_event = messaging[0]
                    if isinstance(first_event, dict):
                        if payload_app_id is None:
                            payload_app_id = _coerce_text(first_event.get("app_id"))
                        recipient = first_event.get("recipient")
                        if isinstance(recipient, dict):
                            recipient_id = _coerce_text(recipient.get("id"))
                            if payload_object == "instagram":
                                instagram_account_id = recipient_id
                            else:
                                page_id = page_id or recipient_id
                        referral = first_event.get("referral")
                        if isinstance(referral, dict) and payload_app_id is None:
                            payload_app_id = _coerce_text(referral.get("app_id"))

                changes = first_entry.get("changes")
                if isinstance(changes, list) and changes:
                    first_change = changes[0]
                    if isinstance(first_change, dict):
                        value = first_change.get("value")
                        if isinstance(value, dict):
                            metadata = value.get("metadata")
                            if isinstance(metadata, dict):
                                page_id = page_id or _coerce_text(metadata.get("phone_number_id"))
                                instagram_account_id = instagram_account_id or _coerce_text(
                                    metadata.get("instagram_account_id")
                                )
                            if payload_app_id is None:
                                payload_app_id = _coerce_text(value.get("app_id"))

    return {
        "object": payload_object,
        "entry_id": entry_id,
        "page_id": page_id,
        "instagram_account_id": instagram_account_id,
        "app_id": payload_app_id or _coerce_text(header_app_id),
    }


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
            raw_calls = value.get("calls") or []
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

                call_payload = msg.get("call")
                call_payload_dict: dict[str, object] = call_payload if isinstance(call_payload, dict) else {}
                call_status = _coerce_text(call_payload_dict.get("call_status"))
                if not call_status:
                    call_status = _coerce_text(call_payload_dict.get("status"))
                call_type = _coerce_text(call_payload_dict.get("type"))
                call_direction = _coerce_text(call_payload_dict.get("call_direction"))
                if not call_direction:
                    call_direction = _coerce_text(call_payload_dict.get("direction"))
                call_id = _coerce_text(call_payload_dict.get("call_id"))
                if not call_id:
                    call_id = _coerce_text(call_payload_dict.get("id"))

                if msg_type == "call":
                    if call_status:
                        status_label = call_status.replace("_", " ").replace("-", " ")
                        if call_direction:
                            body = f"📞 {call_direction.title()} call ({status_label})"
                        else:
                            body = f"📞 Call ({status_label})"
                    elif call_type:
                        body = f"📞 {call_type.title()} call"
                    elif not body:
                        body = "📞 WhatsApp call"
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
                attribution = _extract_whatsapp_attribution(msg)
                if attribution:
                    metadata_payload["attribution"] = attribution
                context = msg.get("context")
                if isinstance(context, dict):
                    context_id = context.get("id")
                    if context_id:
                        metadata_payload["context_message_id"] = context_id
                if location_payload:
                    metadata_payload.update(location_payload)
                if reaction_payload:
                    metadata_payload.update(reaction_payload)
                if msg_type == "call":
                    metadata_payload["type"] = "call"
                    if call_payload_dict:
                        metadata_payload["call"] = call_payload_dict
                    if call_status:
                        metadata_payload["call_status"] = call_status
                    if call_type:
                        metadata_payload["call_type"] = call_type
                    if call_direction:
                        metadata_payload["call_direction"] = call_direction
                    if call_id:
                        metadata_payload["call_id"] = call_id

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

            for call in raw_calls:
                if not isinstance(call, dict):
                    continue
                contact_address = call.get("from")
                call_event_payload: dict[str, object] = call

                call_status = _coerce_text(call_event_payload.get("event"))
                call_type = _coerce_text(call_event_payload.get("type"))
                call_direction = _coerce_text(call_event_payload.get("call_direction"))
                if not call_direction:
                    call_direction = _coerce_text(call_event_payload.get("direction"))
                call_id = _coerce_text(call_event_payload.get("call_id"))
                if not call_id:
                    call_id = _coerce_text(call_event_payload.get("id"))

                if call_status:
                    status_label = call_status.replace("_", " ").replace("-", " ")
                    if call_direction:
                        body = f"📞 {call_direction.title()} call ({status_label})"
                    else:
                        body = f"📞 Call ({status_label})"
                elif call_type:
                    body = f"📞 {call_type.title()} call"
                else:
                    body = "📞 WhatsApp call event"

                received_at = None
                timestamp = call.get("timestamp")
                if timestamp:
                    try:
                        received_at = datetime.fromtimestamp(int(timestamp), tz=UTC)
                    except Exception:
                        received_at = None

                metadata_payload = {
                    "phone_number_id": phone_number_id,
                    "display_phone_number": display_phone_number,
                    "raw": value,
                    "type": "call",
                    "call": call_event_payload,
                }
                if call_status:
                    metadata_payload["call_status"] = call_status
                if call_type:
                    metadata_payload["call_type"] = call_type
                if call_direction:
                    metadata_payload["call_direction"] = call_direction
                if call_id:
                    metadata_payload["call_id"] = call_id

                call_message_payload: WhatsAppWebhookPayload | None = None
                try:
                    call_message_payload = WhatsAppWebhookPayload(
                        contact_address=contact_address or "",
                        contact_name=contact_name,
                        message_id=call_id,
                        body=body,
                        received_at=received_at,
                        metadata=metadata_payload,
                    )
                except Exception as parse_exc:
                    logger.warning(
                        "whatsapp_webhook_message_parse_failed trace_id=%s msg_id=%s",
                        trace_id,
                        call_id or "unknown",
                        exc_info=True,
                    )
                    write_dead_letter(
                        channel="whatsapp",
                        raw_payload=call,
                        error=parse_exc,
                        trace_id=trace_id,
                        message_id=call_id,
                    )
                if call_message_payload:
                    messages.append(call_message_payload)

    return messages


def _parse_meta_whatsapp_status_payload(payload: dict) -> tuple[MetaWebhookPayload | None, int]:
    try:
        meta_payload = MetaWebhookPayload.model_validate(payload)
    except Exception:
        return None, 0

    if meta_payload.object != "whatsapp_business_account":
        return meta_payload, 0

    status_count = 0
    for entry in meta_payload.entry:
        for change in entry.changes or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value") or {}
            status_count += len(value.get("statuses") or [])
    return meta_payload, status_count


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
            parsed_payload = parsed.model_dump()
            enqueued, _ = _enqueue_webhook_task(
                _webhook_tasks().process_whatsapp_webhook.delay,
                channel="whatsapp",
                payload=parsed_payload,
                trace_id=trace_id,
                message_id=parsed.message_id,
            )
            _record_channel_stat("whatsapp", ok=enqueued)
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
            if enqueued:
                return {"status": "ok", "processed": 1}
            return {"status": "accepted", "processed": 0, "failed": 1}
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

        meta_payload, status_count = _parse_meta_whatsapp_status_payload(payload)
        if status_count and meta_payload is not None:
            enqueued, _ = _enqueue_webhook_task(
                _webhook_tasks().process_meta_webhook.delay,
                channel="whatsapp",
                payload=meta_payload.model_dump(),
                trace_id=trace_id,
            )
            _record_channel_stat("whatsapp", ok=enqueued, events=status_count)
            logger.info(
                "whatsapp_status_webhook_enqueued events=%d enqueued=%s",
                status_count,
                enqueued,
            )
            if _should_sample():
                logger.info(
                    "webhook_enqueued channel=whatsapp trace_id=%s object=%s events=%s enqueued=%s latency_ms=%s",
                    trace_id,
                    meta_payload.object,
                    status_count,
                    enqueued,
                    int((time.monotonic() - start_time) * 1000),
                )
            if enqueued:
                return {"status": "ok", "processed": status_count}
            return {"status": "accepted", "processed": 0, "failed": status_count}

        messages = _extract_meta_whatsapp_messages(payload, trace_id=trace_id)
        if not messages:
            _record_channel_stat("whatsapp", ok=True, events=0)
            return {"status": "ok", "processed": 0}

        enqueued_count = 0
        failed_count = 0
        broker_dead = False
        for msg in messages:
            msg_payload = msg.model_dump()
            if broker_dead:
                # Short-circuit: broker already failed, dead-letter remaining messages
                # without waiting for another timeout per message.
                write_dead_letter(
                    channel="whatsapp",
                    raw_payload=msg_payload,
                    error=ConnectionError("Broker unreachable — short-circuited"),
                    trace_id=trace_id,
                    message_id=msg.message_id,
                )
                failed_count += 1
                continue
            enqueued, is_broker_err = _enqueue_webhook_task(
                _webhook_tasks().process_whatsapp_webhook.delay,
                channel="whatsapp",
                payload=msg_payload,
                trace_id=trace_id,
                message_id=msg.message_id,
            )
            if enqueued:
                enqueued_count += 1
            else:
                failed_count += 1
                if is_broker_err:
                    broker_dead = True

        logger.info(
            "whatsapp_webhook_enqueued events=%d enqueued=%d failed=%d",
            len(messages),
            enqueued_count,
            failed_count,
        )
        _record_channel_stat("whatsapp", ok=failed_count == 0, events=len(messages))
        if _should_sample():
            logger.info(
                "webhook_enqueued channel=whatsapp trace_id=%s events=%s enqueued=%s failed=%s latency_ms=%s",
                trace_id,
                len(messages),
                enqueued_count,
                failed_count,
                int((time.monotonic() - start_time) * 1000),
            )
        if failed_count:
            return {"status": "accepted", "processed": enqueued_count, "failed": failed_count}
        return {"status": "ok", "processed": enqueued_count}
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
    enqueued, _ = _enqueue_webhook_task(
        _webhook_tasks().process_email_webhook.delay,
        channel="email",
        payload=payload.model_dump(),
        trace_id=trace_id,
        message_id=payload.message_id,
    )
    _record_channel_stat("email", ok=enqueued)
    if enqueued:
        return {"status": "ok"}
    return {"status": "accepted", "processed": 0, "failed": 1}


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
        wa_secret = settings.get("whatsapp_app_secret")

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

        payload_object: str | None = None
        primary_name = "meta_app_secret"
        primary_secret = app_secret
        secondary_name = "whatsapp_app_secret"
        secondary_secret = wa_secret

        verified_with: str | None = None
        tried_whatsapp_secret = bool(secondary_secret and secondary_secret != primary_secret)
        signature_valid = False
        debug_enabled = _meta_signature_debug_enabled()
        signature_debug_enabled = _meta_signature_compare_debug_enabled()
        raw_body_hash = _raw_body_sha256(body)
        raw_body_hash_prefix = raw_body_hash[:20]
        expected_hash_prefix: str | None = None
        secondary_hash_prefix: str | None = None
        expected_signature: str | None = None
        secondary_signature: str | None = None
        primary_secret_fingerprint = _fingerprint_secret(primary_secret)
        secondary_secret_fingerprint = _fingerprint_secret(secondary_secret)
        content_length = request.headers.get("content-length")
        cf_connecting_ip_present = bool(request.headers.get("CF-Connecting-IP"))
        x_forwarded_for_present = bool(request.headers.get("X-Forwarded-For"))
        primary_signature_valid = False
        secondary_signature_valid = False

        def _verify_signature(secret: str) -> bool:
            try:
                return meta_webhooks.verify_webhook_signature(
                    body,
                    signature,
                    secret,
                    suppress_mismatch_log=True,
                )
            except TypeError:
                # Backward compatibility for patched/mocked callables
                # that still use the legacy 3-argument signature.
                return meta_webhooks.verify_webhook_signature(
                    body,
                    signature,
                    secret,
                )

        def _signature_prefix(secret: str | None) -> str | None:
            if not (debug_enabled or signature_debug_enabled) or not secret:
                return None
            try:
                return meta_webhooks.compute_webhook_signature(body, secret)[:12]
            except Exception:
                logger.debug("meta_webhook_signature_prefix_failed trace_id=%s", trace_id, exc_info=True)
                return None

        def _signature_value(secret: str | None) -> str | None:
            if not signature_debug_enabled or not secret:
                return None
            try:
                return meta_webhooks.compute_webhook_signature(body, secret)
            except Exception:
                logger.debug("meta_webhook_signature_value_failed trace_id=%s", trace_id, exc_info=True)
                return None

        expected_hash_prefix = _signature_prefix(primary_secret)
        expected_signature = _signature_value(primary_secret)
        if secondary_secret and secondary_secret != primary_secret:
            secondary_hash_prefix = _signature_prefix(secondary_secret)
            secondary_signature = _signature_value(secondary_secret)

        try:
            signature_valid = _verify_signature(primary_secret)
            primary_signature_valid = signature_valid
            if signature_valid:
                verified_with = primary_name
        except Exception as exc:
            logger.warning("meta_webhook_signature_validation_failed error=%s", exc)
            signature_valid = False

        if not signature_valid and secondary_secret and secondary_secret != primary_secret:
            tried_whatsapp_secret = secondary_name == "whatsapp_app_secret" or primary_name == "whatsapp_app_secret"
            try:
                signature_valid = _verify_signature(secondary_secret)
                secondary_signature_valid = signature_valid
                if signature_valid:
                    verified_with = secondary_name
                    if secondary_name == "whatsapp_app_secret":
                        logger.info("meta_webhook_verified_with_whatsapp_secret")
            except Exception:
                logger.debug("meta_webhook_secondary_signature_check_failed trace_id=%s", trace_id, exc_info=True)
        if debug_enabled:
            logger.info(
                "meta_webhook_signature_debug trace_id=%s signature_present=%s signature_valid=%s "
                "raw_body_hash=%s expected_hash_prefix=%s secondary_hash_prefix=%s verified_with=%s",
                trace_id,
                bool(signature),
                signature_valid,
                raw_body_hash,
                expected_hash_prefix,
                secondary_hash_prefix,
                verified_with,
            )
        if signature_debug_enabled:
            expected_header = f"sha256={expected_signature}" if expected_signature else None
            secondary_header = f"sha256={secondary_signature}" if secondary_signature else None
            signature_match = bool(signature and expected_header and hmac.compare_digest(signature, expected_header))
            secondary_signature_match = bool(
                signature and secondary_header and hmac.compare_digest(signature, secondary_header)
            )
            signature_header_prefix = signature[:19] if signature else None
            computed_signature_prefix = expected_header[:19] if expected_header else None
            secondary_computed_signature_prefix = secondary_header[:19] if secondary_header else None
            debug_context = _extract_meta_signature_debug_context(body, dict(request.headers))
            logger.info(
                "meta_webhook_signature_compare trace_id=%s signature_match=%s secondary_signature_match=%s "
                "signature_header_prefix=%s computed_signature_prefix=%s secondary_computed_signature_prefix=%s "
                "body_hash_prefix=%s content_length=%s body_bytes=%s "
                "cf_connecting_ip_present=%s x_forwarded_for_present=%s object=%s entry_id=%s "
                "app_id=%s page_id=%s instagram_account_id=%s primary_secret_fingerprint=%s "
                "secondary_secret_fingerprint=%s primary_signature_valid=%s secondary_signature_valid=%s "
                "final_verified_with=%s",
                trace_id,
                signature_match,
                secondary_signature_match,
                signature_header_prefix,
                computed_signature_prefix,
                secondary_computed_signature_prefix,
                raw_body_hash_prefix,
                content_length,
                len(body or b""),
                cf_connecting_ip_present,
                x_forwarded_for_present,
                debug_context.get("object"),
                debug_context.get("entry_id"),
                debug_context.get("app_id"),
                debug_context.get("page_id"),
                debug_context.get("instagram_account_id"),
                primary_secret_fingerprint,
                secondary_secret_fingerprint,
                primary_signature_valid,
                secondary_signature_valid,
                verified_with,
            )
        if not signature_valid:
            invalid_context = _extract_meta_signature_debug_context(body, dict(request.headers))
            logger.info(
                "meta_webhook_signature_invalid trace_id=%s signature_present=%s body_bytes=%s "
                "payload_object=%s tried_whatsapp_secret=%s verified_with=%s raw_body_hash=%s "
                "expected_hash_prefix=%s secondary_hash_prefix=%s object=%s entry_id=%s app_id=%s "
                "page_id=%s instagram_account_id=%s primary_secret_fingerprint=%s "
                "secondary_secret_fingerprint=%s primary_signature_valid=%s secondary_signature_valid=%s",
                trace_id,
                bool(signature),
                len(body or b""),
                payload_object,
                tried_whatsapp_secret,
                verified_with,
                raw_body_hash,
                expected_hash_prefix,
                secondary_hash_prefix,
                invalid_context.get("object"),
                invalid_context.get("entry_id"),
                invalid_context.get("app_id"),
                invalid_context.get("page_id"),
                invalid_context.get("instagram_account_id"),
                primary_secret_fingerprint,
                secondary_secret_fingerprint,
                primary_signature_valid,
                secondary_signature_valid,
            )
            return Response(status_code=401)

        try:
            payload = MetaWebhookPayload.model_validate_json(body)
            payload_object = payload.object
        except Exception as exc:
            logger.warning("meta_webhook_invalid_payload error=%s", exc)
            _record_channel_stat("meta", ok=False)
            return JSONResponse(
                status_code=400,
                content={"status": "error", "detail": "Invalid payload"},
            )

        event_count = sum(len(entry.messaging or []) + len(entry.changes or []) for entry in payload.entry)
        enqueue_result = _enqueue_webhook_task(
            _webhook_tasks().process_meta_webhook.delay,
            channel="meta",
            payload=payload.model_dump(),
            trace_id=trace_id,
        )
        enqueued = bool(enqueue_result[0] if isinstance(enqueue_result, tuple) else enqueue_result)
        logger.info("meta_webhook_enqueued type=%s events=%d enqueued=%s", payload.object, event_count, enqueued)
        _record_channel_stat("meta", ok=enqueued, events=event_count)
        if _should_sample():
            logger.info(
                "webhook_enqueued channel=meta trace_id=%s object=%s events=%s enqueued=%s latency_ms=%s",
                trace_id,
                payload.object,
                event_count,
                enqueued,
                int((time.monotonic() - start_time) * 1000),
            )
        if enqueued:
            return {"status": "ok", "processed": event_count}
        return {"status": "accepted", "processed": 0, "failed": event_count}
    except Exception:
        logger.exception("meta_webhook_unhandled")
        _record_channel_stat("meta", ok=False)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": "Unhandled error"},
        )
