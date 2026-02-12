"""Shared utility for persisting failed webhook payloads to the dead letter table."""

import logging
import traceback

from app.db import SessionLocal
from app.models.webhook_dead_letter import WebhookDeadLetter

logger = logging.getLogger(__name__)


def write_dead_letter(
    channel: str,
    raw_payload: dict | str | bytes,
    error: str | Exception,
    trace_id: str | None = None,
    message_id: str | None = None,
) -> None:
    """Persist a failed inbound webhook/message payload for later inspection.

    This function opens its own DB session so it is safe to call even when
    the caller's session is dirty or closed.

    Args:
        channel: Channel identifier (e.g. "whatsapp", "email", "meta",
                 "facebook_messenger", "instagram_dm", "facebook_comment",
                 "instagram_comment", "smtp").
        raw_payload: The original payload dict (or stringified bytes for SMTP).
        error: The error message or exception.
        trace_id: Optional trace/correlation ID.
        message_id: Optional provider message ID.
    """
    if isinstance(error, Exception):
        # Use format_exception() on the object directly — format_exc() reads
        # sys.exc_info() which may hold a different exception (e.g.
        # MaxRetriesExceededError) when called from nested except blocks.
        error_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    else:
        error_str = str(error)

    if isinstance(raw_payload, bytes):
        raw_payload = {"raw_bytes_b64": None, "note": "binary payload — see logs"}
    if isinstance(raw_payload, str):
        raw_payload = {"raw_text": raw_payload[:8000]}

    session = SessionLocal()
    try:
        dl = WebhookDeadLetter(
            channel=channel,
            trace_id=trace_id,
            message_id=message_id,
            raw_payload=raw_payload,
            error=error_str[:4000] if error_str else None,
        )
        session.add(dl)
        session.commit()
        logger.info(
            "webhook_dead_letter_written channel=%s trace_id=%s message_id=%s",
            channel,
            trace_id,
            message_id,
        )
    except Exception:
        session.rollback()
        logger.exception(
            "webhook_dead_letter_write_failed channel=%s trace_id=%s",
            channel,
            trace_id,
        )
    finally:
        session.close()
