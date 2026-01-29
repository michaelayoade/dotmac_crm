from __future__ import annotations

import base64
import email
import imaplib
import poplib
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from datetime import timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.connector import ConnectorConfig
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.models.crm.conversation import MessageAttachment
from app.schemas.crm.inbox import EmailWebhookPayload
from app.services.crm import inbox as inbox_service
from app.services.common import coerce_uuid
from app.logging import get_logger

logger = get_logger(__name__)


def _decode_header(value: str | None) -> str | None:
    if not value:
        return None
    parts = decode_header(value)
    decoded = ""
    for fragment, encoding in parts:
        if isinstance(fragment, bytes):
            decoded += fragment.decode(encoding or "utf-8", errors="replace")
        else:
            decoded += fragment
    return decoded.strip()


def _normalize_email_address(address: str | None) -> str | None:
    if not address:
        return None
    candidate = address.strip().lower()
    return candidate or None


def _self_addresses_from_config(config: ConnectorConfig, auth_config: dict) -> set[str]:
    addresses: set[str] = set()
    if isinstance(auth_config, dict):
        for key in ("username", "from_email", "email"):
            value = auth_config.get(key)
            normalized = _normalize_email_address(value) if isinstance(value, str) else None
            if normalized:
                addresses.add(normalized)
    metadata = config.metadata_ if isinstance(config.metadata_, dict) else {}
    smtp_value = metadata.get("smtp")
    smtp_config: dict[str, object] = smtp_value if isinstance(smtp_value, dict) else {}
    for key in ("username", "from_email", "from"):
        value = smtp_config.get(key)
        normalized = _normalize_email_address(value) if isinstance(value, str) else None
        if normalized:
            addresses.add(normalized)
    return addresses


def _is_self_sender(from_addr: str | None, config: ConnectorConfig, auth_config: dict) -> bool:
    sender = _normalize_email_address(from_addr)
    if not sender:
        return False
    return sender in _self_addresses_from_config(config, auth_config)


def _payload_to_bytes(value: object | None) -> bytes:
    if isinstance(value, bytes):
        return value
    if value is None:
        return b""
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace")
    return str(value).encode("utf-8", errors="replace")


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = part.get("Content-Disposition", "")
            if content_type == "text/plain" and "attachment" not in disposition:
                payload = _payload_to_bytes(part.get_payload(decode=True))
                charset = part.get_content_charset() or "utf-8"
                return (payload or b"").decode(charset, errors="replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = _payload_to_bytes(part.get_payload(decode=True))
                charset = part.get_content_charset() or "utf-8"
                return (payload or b"").decode(charset, errors="replace")
        return ""
    payload = _payload_to_bytes(msg.get_payload(decode=True))
    charset = msg.get_content_charset() or "utf-8"
    return (payload or b"").decode(charset, errors="replace")


def _extract_attachments(msg: email.message.Message) -> list[dict[str, object]]:
    attachments: list[dict[str, object]] = []
    if not msg.is_multipart():
        return attachments
    for part in msg.walk():
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        content_id = part.get("Content-ID")
        if "attachment" not in disposition and not filename and not content_id:
            continue
        raw_payload = part.get_payload(decode=True)
        if isinstance(raw_payload, bytes):
            payload = raw_payload
        elif raw_payload is None:
            payload = b""
        else:
            payload = str(raw_payload).encode("utf-8", errors="replace")
        attachments.append(
            {
                "file_name": _decode_header(filename) if filename else None,
                "mime_type": part.get_content_type(),
                "file_size": len(payload),
                "content_base64": base64.b64encode(payload).decode("ascii"),
                "content_id": content_id,
            }
        )
    return attachments


def _store_attachments(db: Session, message_id, attachments: list[dict]) -> None:
    if not attachments:
        return
    db.query(MessageAttachment).filter(MessageAttachment.message_id == message_id).delete()
    for attachment in attachments:
        db.add(
            MessageAttachment(
                message_id=message_id,
                file_name=attachment.get("file_name"),
                mime_type=attachment.get("mime_type"),
                file_size=attachment.get("file_size"),
                metadata_={
                    "content_base64": attachment.get("content_base64"),
                    "content_id": attachment.get("content_id"),
                },
            )
        )
    db.commit()


def _imap_poll(
    db: Session,
    config: ConnectorConfig,
    imap_config: dict,
    auth_config: dict,
    target_id: str | None = None,
) -> int:
    host_value = imap_config.get("host")
    host = str(host_value) if host_value else None
    port = int(imap_config.get("port") or 993)
    use_ssl = bool(imap_config.get("use_ssl", True))
    username = auth_config.get("username")
    password = auth_config.get("password")
    if (
        not host
        or not isinstance(username, str)
        or not isinstance(password, str)
        or not username
        or not password
    ):
        raise HTTPException(status_code=400, detail="IMAP config incomplete")

    last_uid = None
    metadata = dict(config.metadata_ or {})
    if isinstance(metadata.get("imap_last_uid"), int):
        last_uid = metadata.get("imap_last_uid")

    client = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
    client.login(username, password)
    mailbox = imap_config.get("mailbox", "INBOX")
    client.select(mailbox if isinstance(mailbox, str) else "INBOX")

    if last_uid:
        criteria = f"(UID {last_uid + 1}:*)"
        _, data = client.uid("search", "UTF-8", criteria)
    else:
        _, data = client.uid("search", "UTF-8", "UNSEEN")
    uids = data[0].split() if data and data[0] else []

    processed = 0
    for uid in uids:
        _, msg_data = client.uid("fetch", uid, "(RFC822)")
        if not msg_data or not msg_data[0]:
            continue
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        from_header = msg.get("From") or ""
        from_addr = parseaddr(from_header)[1]
        if _is_self_sender(from_addr, config, auth_config):
            try:
                uid_int = int(uid)
                if last_uid is None or uid_int > last_uid:
                    last_uid = uid_int
            except ValueError:
                pass
            continue
        subject = _decode_header(msg.get("Subject"))
        message_id = msg.get("Message-ID")
        reply_to = msg.get_all("Reply-To") or []
        to_addrs = msg.get_all("To") or []
        cc_addrs = msg.get_all("Cc") or []
        in_reply_to = msg.get("In-Reply-To")
        references = msg.get("References")
        received_at = None
        date_header = msg.get("Date")
        if date_header:
            try:
                parsed_date = parsedate_to_datetime(date_header)
                if parsed_date:
                    received_at = (
                        parsed_date.astimezone(timezone.utc)
                        if parsed_date.tzinfo
                        else parsed_date.replace(tzinfo=timezone.utc)
                    )
            except Exception:
                received_at = None
        body = _extract_body(msg)
        attachments = _extract_attachments(msg)
        uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
        payload = EmailWebhookPayload(
            contact_address=from_addr,
            contact_name=_decode_header(parseaddr(from_header)[0]) or None,
            message_id=message_id,
            channel_target_id=coerce_uuid(target_id) if target_id else None,
            subject=subject,
            body=body or "",
            received_at=received_at,
            metadata={
                "source": "imap",
                "uid": uid_str,
                "reply_to": reply_to,
                "to": to_addrs,
                "cc": cc_addrs,
                "in_reply_to": in_reply_to,
                "references": references,
            },
        )
        message = inbox_service.receive_email_message(db, payload)
        if message:
            _store_attachments(db, message.id, attachments)
            processed += 1
        try:
            uid_int = int(uid)
            if last_uid is None or uid_int > last_uid:
                last_uid = uid_int
        except ValueError:
            pass

    if last_uid is not None:
        metadata["imap_last_uid"] = last_uid
        config.metadata_ = metadata
        db.commit()

    client.logout()
    return processed


def _pop3_poll(
    db: Session,
    config: ConnectorConfig,
    pop3_config: dict,
    auth_config: dict,
    target_id: str | None = None,
) -> int:
    host_value = pop3_config.get("host")
    host = str(host_value) if host_value else None
    port = int(pop3_config.get("port") or 995)
    use_ssl = bool(pop3_config.get("use_ssl", True))
    username = auth_config.get("username")
    password = auth_config.get("password")
    if (
        not host
        or not isinstance(username, str)
        or not isinstance(password, str)
        or not username
        or not password
    ):
        raise HTTPException(status_code=400, detail="POP3 config incomplete")

    metadata = dict(config.metadata_ or {})
    last_uidl = metadata.get("pop3_last_uidl")

    client = poplib.POP3_SSL(host, port) if use_ssl else poplib.POP3(host, port)
    client.user(username)
    client.pass_(password)

    resp, listings, _ = client.uidl()
    if not listings:
        client.quit()
        return 0

    processed = 0
    for entry in listings:
        parts = entry.decode().split()
        if len(parts) != 2:
            continue
        msg_num, uidl = parts
        if last_uidl and uidl <= last_uidl:
            continue
        _, lines, _ = client.retr(int(msg_num))
        raw = b"\n".join(lines)
        msg = email.message_from_bytes(raw)
        from_header = msg.get("From") or ""
        from_addr = parseaddr(from_header)[1]
        if _is_self_sender(from_addr, config, auth_config):
            last_uidl = uidl
            continue
        subject = _decode_header(msg.get("Subject"))
        message_id = msg.get("Message-ID")
        reply_to = msg.get_all("Reply-To") or []
        to_addrs = msg.get_all("To") or []
        cc_addrs = msg.get_all("Cc") or []
        in_reply_to = msg.get("In-Reply-To")
        references = msg.get("References")
        received_at = None
        date_header = msg.get("Date")
        if date_header:
            try:
                parsed_date = parsedate_to_datetime(date_header)
                if parsed_date:
                    received_at = (
                        parsed_date.astimezone(timezone.utc)
                        if parsed_date.tzinfo
                        else parsed_date.replace(tzinfo=timezone.utc)
                    )
            except Exception:
                received_at = None
        body = _extract_body(msg)
        attachments = _extract_attachments(msg)
        payload = EmailWebhookPayload(
            contact_address=from_addr,
            contact_name=_decode_header(parseaddr(from_header)[0]) or None,
            message_id=message_id,
            channel_target_id=coerce_uuid(target_id) if target_id else None,
            subject=subject,
            body=body or "",
            received_at=received_at,
            metadata={
                "source": "pop3",
                "uidl": uidl,
                "reply_to": reply_to,
                "to": to_addrs,
                "cc": cc_addrs,
                "in_reply_to": in_reply_to,
                "references": references,
            },
        )
        message = inbox_service.receive_email_message(db, payload)
        if message:
            _store_attachments(db, message.id, attachments)
            processed += 1
        last_uidl = uidl

    if last_uidl:
        metadata["pop3_last_uidl"] = last_uidl
        config.metadata_ = metadata
        db.commit()

    client.quit()
    return processed


def poll_email_connector(db: Session, config: ConnectorConfig) -> dict:
    target_id = None
    target = (
        db.query(IntegrationTarget)
        .filter(IntegrationTarget.connector_config_id == config.id)
        .filter(IntegrationTarget.target_type == IntegrationTargetType.crm)
        .filter(IntegrationTarget.is_active.is_(True))
        .order_by(IntegrationTarget.created_at.desc())
        .first()
    )
    if target:
        target_id = str(target.id)
    metadata = config.metadata_ or {}
    auth_config = config.auth_config or {}
    processed = 0
    imap_config = metadata.get("imap") if isinstance(metadata, dict) else None
    pop3_config = metadata.get("pop3") if isinstance(metadata, dict) else None
    if isinstance(imap_config, dict):
        logger.info(
            "EMAIL_CONNECTOR_FOUND connector_id=%s protocol=imap host=%s mailbox=%s",
            config.id,
            imap_config.get("host"),
            imap_config.get("mailbox"),
        )
        try:
            processed += _imap_poll(
                db,
                config,
                imap_config or {},
                auth_config,
                target_id,
            )
        except Exception as exc:
            logger.info(
                "EMAIL_POLL_EXIT reason=auth_or_connection_failure protocol=imap connector_id=%s error=%s",
                config.id,
                exc,
            )
            raise
    if isinstance(pop3_config, dict):
        logger.info(
            "EMAIL_CONNECTOR_FOUND connector_id=%s protocol=pop3 host=%s mailbox=%s",
            config.id,
            pop3_config.get("host"),
            pop3_config.get("mailbox"),
        )
        try:
            processed += _pop3_poll(
                db,
                config,
                pop3_config or {},
                auth_config,
                target_id,
            )
        except Exception as exc:
            logger.info(
                "EMAIL_POLL_EXIT reason=auth_or_connection_failure protocol=pop3 connector_id=%s error=%s",
                config.id,
                exc,
            )
            raise
    return {"processed": processed}
