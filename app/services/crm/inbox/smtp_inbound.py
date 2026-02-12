"""Lightweight SMTP server to ingest inbound CRM email messages."""

from __future__ import annotations

import base64
import os
import sys
from collections.abc import Iterable
from datetime import UTC
from email import message_from_bytes, policy
from email.header import decode_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from typing import Any

from app.db import SessionLocal
from app.logging import get_logger
from app.models.crm.conversation import MessageAttachment
from app.schemas.crm.inbox import EmailWebhookPayload
from app.services.crm import inbox as inbox_service
from app.services.webhook_dead_letter import write_dead_letter

SMTPController: Any
try:
    from aiosmtpd.controller import Controller as SMTPController
except ModuleNotFoundError:
    SMTPController = None

logger = get_logger(__name__)


def _decode_header_value(value: str | None) -> str | None:
    """Decode RFC2047 header values into readable text."""
    if not value:
        return None
    parts = decode_header(value)
    decoded = ""
    for fragment, encoding in parts:
        if isinstance(fragment, bytes):
            decoded += fragment.decode(encoding or "utf-8", errors="replace")
        else:
            decoded += fragment
    return decoded.strip() or None


def _extract_bodies(msg) -> tuple[str | None, str | None]:
    """Extract text/plain and text/html bodies from a message."""
    text_body = None
    html_body = None
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            content_type = part.get_content_type()
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            content = payload.decode(charset, errors="replace")
            if content_type == "text/plain" and text_body is None:
                text_body = content
            elif content_type == "text/html" and html_body is None:
                html_body = content
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        content = payload.decode(charset, errors="replace")
        if msg.get_content_type() == "text/html":
            html_body = content
        else:
            text_body = content
    return text_body, html_body


def _extract_attachments(msg) -> list[dict]:
    """Extract attachments and return metadata with base64 content."""
    attachments = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        content_id = part.get("Content-ID")
        if "attachment" not in disposition and not filename and not content_id:
            continue
        payload = part.get_payload(decode=True) or b""
        if not payload:
            continue
        attachments.append(
            {
                "file_name": _decode_header_value(filename) if filename else None,
                "mime_type": part.get_content_type(),
                "file_size": len(payload),
                "content_id": content_id,
                "content_base64": base64.b64encode(payload).decode("ascii"),
            }
        )
    return attachments


def _parse_addresses(values: Iterable[str]) -> list[str]:
    """Parse a list of address headers into email addresses."""
    return [addr for _, addr in getaddresses(values) if addr]


def _normalize_email_address(address: str | None) -> str | None:
    if not address:
        return None
    candidate = address.strip().lower()
    return candidate or None


def _handle_message(
    mailfrom: str,
    rcpttos: list[str],
    data: bytes,
    allowed_recipients: set[str] | None = None,
) -> None:
    try:
        msg = message_from_bytes(data, policy=policy.default)
        from_name, from_addr = parseaddr(msg.get("From") or "")
        if not from_addr:
            from_addr = mailfrom
        to_addresses = _parse_addresses(msg.get_all("To", []))
        if not to_addresses and rcpttos:
            to_addresses = list(rcpttos)
        cc_addresses = _parse_addresses(msg.get_all("Cc", []))
        subject = _decode_header_value(msg.get("Subject"))
        message_id = msg.get("Message-ID")
        received_at = None
        date_header = msg.get("Date")
        if date_header:
            try:
                parsed_date = parsedate_to_datetime(date_header)
                if parsed_date:
                    received_at = parsed_date.astimezone(UTC) if parsed_date.tzinfo else parsed_date.replace(tzinfo=UTC)
            except Exception:
                received_at = None
        text_body, html_body = _extract_bodies(msg)
        body = (text_body or html_body or "").strip()
        if not body:
            body = "(no content)"
        if not from_addr:
            logger.warning("smtp_inbound_missing_from recipient=%s", ",".join(to_addresses))
            return
        if allowed_recipients:
            normalized_from = _normalize_email_address(from_addr)
            if normalized_from and normalized_from in allowed_recipients:
                logger.info("smtp_inbound_skip_self from=%s", from_addr)
                return

        metadata: dict[str, Any] = {
            "smtp": {
                "from_raw": msg.get("From"),
                "to_raw": msg.get("To"),
                "cc": cc_addresses,
                "recipients": to_addresses,
            }
        }
        if html_body:
            metadata["html_body"] = html_body

        payload = EmailWebhookPayload(
            contact_address=from_addr,
            contact_name=_decode_header_value(from_name) if from_name else None,
            message_id=message_id,
            subject=subject,
            body=body,
            received_at=received_at,
            metadata=metadata,
        )

        db = SessionLocal()
        try:
            message = inbox_service.receive_email_message(db, payload)
            if not message:
                return
            attachments = _extract_attachments(msg)
            if attachments:
                existing = db.query(MessageAttachment).filter(MessageAttachment.message_id == message.id).count()
                if existing == 0:
                    for attachment in attachments:
                        db.add(
                            MessageAttachment(
                                message_id=message.id,
                                file_name=attachment["file_name"],
                                mime_type=attachment["mime_type"],
                                file_size=attachment["file_size"],
                                metadata_={
                                    "content_base64": attachment["content_base64"],
                                    "content_id": attachment["content_id"],
                                },
                            )
                        )
                    db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.exception("smtp_inbound_processing_failed")
        # Persist enough context to diagnose/replay — avoid storing full
        # raw bytes (may be very large with attachments).
        write_dead_letter(
            channel="smtp",
            raw_payload={
                "mailfrom": mailfrom,
                "rcpttos": rcpttos,
                "data_size": len(data) if data else 0,
            },
            error=exc,
            message_id=None,
        )


class CRMInboundSMTPHandler:
    """SMTP handler that converts inbound emails into CRM inbox messages."""

    def __init__(self, allowed_recipients: set[str] | None):
        if allowed_recipients:
            normalized = {addr for addr in (_normalize_email_address(raw) for raw in allowed_recipients) if addr}
            self.allowed_recipients: set[str] | None = normalized
        else:
            self.allowed_recipients = None

    async def handle_DATA(self, server, session, envelope):
        to_addresses = list(envelope.rcpt_tos or [])
        if self.allowed_recipients:
            normalized_to = {_normalize_email_address(addr) for addr in to_addresses if _normalize_email_address(addr)}
            matched = any(addr in self.allowed_recipients for addr in normalized_to)
            if not matched:
                logger.info(
                    "smtp_inbound_skip_recipient from=%s to=%s",
                    envelope.mail_from,
                    ",".join(to_addresses),
                )
                return "250 OK"
        _handle_message(
            envelope.mail_from,
            to_addresses,
            envelope.content or b"",
            self.allowed_recipients,
        )
        return "250 OK"


_SMTP_CONTROLLER: Any | None = None


def start_smtp_inbound_server() -> None:
    """Start the inbound SMTP server in a background thread."""
    global _SMTP_CONTROLLER
    if SMTPController is None:
        logger.warning(
            "smtp_inbound_unavailable reason=missing_aiosmtpd python=%s",
            ".".join(map(str, sys.version_info[:3])),
        )
        return
    enabled = os.getenv("CRM_SMTP_INBOUND_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not enabled:
        return

    host = os.getenv("CRM_SMTP_INBOUND_HOST", "127.0.0.1")
    port = int(os.getenv("CRM_SMTP_INBOUND_PORT", "2525"))
    recipients_env = os.getenv("CRM_SMTP_INBOUND_RECIPIENTS", "").strip()
    allowed_recipients = None
    if recipients_env:
        allowed_recipients = {addr.strip() for addr in recipients_env.split(",") if addr.strip()}

    handler = CRMInboundSMTPHandler(allowed_recipients)
    controller = SMTPController(handler, hostname=host, port=port)
    controller.start()
    _SMTP_CONTROLLER = controller
    logger.info("smtp_inbound_server_start host=%s port=%s", host, port)


def stop_smtp_inbound_server() -> None:
    """Stop the inbound SMTP server if running."""
    global _SMTP_CONTROLLER
    if _SMTP_CONTROLLER:
        try:
            _SMTP_CONTROLLER.stop()
        except Exception:
            logger.exception("smtp_inbound_server_stop_failed")
        _SMTP_CONTROLLER = None


# Backwards-compatible aliases
start_smtp_server = start_smtp_inbound_server
stop_smtp_server = stop_smtp_inbound_server
