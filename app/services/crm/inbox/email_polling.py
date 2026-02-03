from __future__ import annotations

import base64
import email
import imaplib
import poplib
import re
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

EMAIL_POLL_CONNECT_TIMEOUT = 30
POP3_UIDL_HISTORY_LIMIT = 500


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


def _extract_uid_from_fetch_header(header: object) -> str | None:
    if isinstance(header, (bytes, bytearray)):
        header_bytes = header
    else:
        header_bytes = str(header).encode("utf-8", errors="replace")
    match = re.search(rb"UID\s+(\d+)", header_bytes)
    if not match:
        return None
    return match.group(1).decode("utf-8", errors="replace")


def poll_email_inbox(db: Session, connector_config_id: str) -> dict:
    config = db.get(ConnectorConfig, coerce_uuid(connector_config_id))
    if not config:
        raise HTTPException(status_code=404, detail="Connector config not found")
    return poll_email_connector(db, config)


class EmailPoller:
    @staticmethod
    def poll(db: Session, connector_config_id: str) -> dict:
        return poll_email_inbox(db, connector_config_id)


# Singleton instance
email_poller = EmailPoller()



def _extract_bodies(msg: email.message.Message) -> tuple[str | None, str | None]:
    text_body = None
    html_body = None
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = part.get("Content-Disposition", "")
            if "attachment" in disposition:
                continue
            payload = _payload_to_bytes(part.get_payload(decode=True))
            charset = part.get_content_charset() or "utf-8"
            content = (payload or b"").decode(charset, errors="replace")
            if content_type == "text/plain" and text_body is None:
                text_body = content
            elif content_type == "text/html" and html_body is None:
                html_body = content
    else:
        payload = _payload_to_bytes(msg.get_payload(decode=True))
        charset = msg.get_content_charset() or "utf-8"
        content = (payload or b"").decode(charset, errors="replace")
        if msg.get_content_type() == "text/html":
            html_body = content
        else:
            text_body = content
    return text_body, html_body


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

    metadata = dict(config.metadata_ or {})
    last_uid = metadata.get("imap_last_uid") if isinstance(metadata.get("imap_last_uid"), int) else None

    client = (
        imaplib.IMAP4_SSL(host, port, timeout=EMAIL_POLL_CONNECT_TIMEOUT)
        if use_ssl
        else imaplib.IMAP4(host, port, timeout=EMAIL_POLL_CONNECT_TIMEOUT)
    )
    client.login(username, password)
    mailbox = imap_config.get("mailbox", "INBOX")
    mailbox_value = mailbox.strip() if isinstance(mailbox, str) and mailbox.strip() else "INBOX"

    def _uid_search(search_criteria: str) -> list[bytes]:
        # Some IMAP servers reject UTF-8 unless explicitly enabled; fall back to US-ASCII.
        for charset in ("UTF-8", "US-ASCII", None):
            try:
                status, data = client.uid("search", charset, search_criteria)
            except imaplib.IMAP4.error:
                continue
            logger.info(
                "EMAIL_IMAP_SEARCH mailbox=%s charset=%s status=%s",
                mailbox_value,
                charset,
                status,
            )
            if status == "OK" and data:
                return data
        return []

    status, selected = client.select(mailbox_value)
    selected_count = 0
    if status == "OK" and selected and selected[0]:
        try:
            selected_count = int(selected[0])
        except (TypeError, ValueError):
            selected_count = 0
    logger.info(
        "EMAIL_IMAP_SELECT mailbox=%s status=%s messages=%s",
        mailbox_value,
        status,
        selected,
    )

    processed = 0
    search_all = bool(imap_config.get("search_all"))
    criteria_label = "ALL" if search_all else "UNSEEN"
    if last_uid:
        criteria = f"(UID {last_uid + 1}:*)"
        data = _uid_search(criteria)
    else:
        data = _uid_search(criteria_label)
    uids = data[0].split() if data and data[0] else []
    logger.info(
        "EMAIL_IMAP_UIDS mailbox=%s criteria=%s count=%s",
        mailbox_value,
        criteria if last_uid else criteria_label,
        len(uids),
    )
    if not uids and selected_count > 0:
        fallback_criteria = criteria_label
        status_all, data_all = client.search(None, fallback_criteria)
        seq_nums = data_all[0].split() if status_all == "OK" and data_all and data_all[0] else []
        logger.info(
            "EMAIL_IMAP_SEARCH_FALLBACK mailbox=%s criteria=%s status=%s count=%s",
            mailbox_value,
            fallback_criteria,
            status_all,
            len(seq_nums),
        )
        for seq in seq_nums:
            seq_value = seq.decode() if isinstance(seq, bytes) else str(seq)
            _, msg_data = client.fetch(seq_value, "(UID RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            header, raw = msg_data[0] if isinstance(msg_data[0], tuple) else (None, None)
            if not raw:
                continue
            uid_value = _extract_uid_from_fetch_header(header)
            if last_uid is not None:
                if not uid_value or not uid_value.isdigit():
                    continue
                if int(uid_value) <= last_uid:
                    continue
            msg = email.message_from_bytes(raw)
            from_header = msg.get("From") or ""
            from_addr = parseaddr(from_header)[1]
            if _is_self_sender(from_addr, config, auth_config):
                if uid_value and uid_value.isdigit():
                    uid_int = int(uid_value)
                    if last_uid is None or uid_int > last_uid:
                        last_uid = uid_int
                continue
            subject = _decode_header(msg.get("Subject"))
            if isinstance(subject, str):
                subject = subject.strip()
                if len(subject) > 200:
                    subject = subject[:200]
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
            text_body, html_body = _extract_bodies(msg)
            body = (text_body or html_body or "").strip()
            if not body:
                body = subject or "[No content]"
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
                    "source": "imap",
                    "mailbox": mailbox_value,
                    "uid": uid_value,
                    "reply_to": reply_to,
                    "to": to_addrs,
                    "cc": cc_addrs,
                    "in_reply_to": in_reply_to,
                    "references": references,
                    "html_body": html_body,
                },
            )
            message = inbox_service.receive_email_message(db, payload)
            if message:
                _store_attachments(db, message.id, attachments)
                processed += 1
            if uid_value and uid_value.isdigit():
                uid_int = int(uid_value)
                if last_uid is None or uid_int > last_uid:
                    last_uid = uid_int
        if last_uid is not None:
            metadata["imap_last_uid"] = last_uid
            config.metadata_ = metadata
            db.commit()
        client.logout()
        return processed
    if uids:
        try:
            first_uid = uids[0].decode() if isinstance(uids[0], bytes) else str(uids[0])
            last_uid_val = uids[-1].decode() if isinstance(uids[-1], bytes) else str(uids[-1])
            logger.info(
                "EMAIL_IMAP_UID_RANGE mailbox=%s first=%s last=%s",
                mailbox_value,
                first_uid,
                last_uid_val,
            )
        except Exception:
            pass

    for uid in uids:
        uid_value = uid.decode() if isinstance(uid, bytes) else str(uid)
        _, msg_data = client.uid("fetch", uid_value, "(RFC822)")
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
        if isinstance(subject, str):
            subject = subject.strip()
            if len(subject) > 200:
                subject = subject[:200]
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
        text_body, html_body = _extract_bodies(msg)
        body = (text_body or html_body or "").strip()
        if not body:
            body = subject or "[No content]"
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
                "mailbox": mailbox_value,
                "uid": uid_str,
                "reply_to": reply_to,
                "to": to_addrs,
                "cc": cc_addrs,
                "in_reply_to": in_reply_to,
                "references": references,
                "html_body": html_body,
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
    seen_uidls_raw = metadata.get("pop3_seen_uidls")
    if not isinstance(seen_uidls_raw, list):
        seen_uidls_raw = []
    seen_uidls = {uid for uid in seen_uidls_raw if isinstance(uid, str)}
    if isinstance(last_uidl, str) and last_uidl:
        seen_uidls.add(last_uidl)

    client = (
        poplib.POP3_SSL(host, port, timeout=EMAIL_POLL_CONNECT_TIMEOUT)
        if use_ssl
        else poplib.POP3(host, port, timeout=EMAIL_POLL_CONNECT_TIMEOUT)
    )
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
        if uidl in seen_uidls:
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
        if isinstance(subject, str):
            subject = subject.strip()
            if len(subject) > 200:
                subject = subject[:200]
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
        text_body, html_body = _extract_bodies(msg)
        body = (text_body or html_body or "").strip()
        if not body:
            body = subject or "[No content]"
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
                "html_body": html_body,
            },
        )
        message = inbox_service.receive_email_message(db, payload)
        if message:
            _store_attachments(db, message.id, attachments)
            processed += 1
        last_uidl = uidl
        seen_uidls.add(uidl)
        seen_uidls_raw.append(uidl)
        if len(seen_uidls_raw) > POP3_UIDL_HISTORY_LIMIT:
            seen_uidls_raw = seen_uidls_raw[-POP3_UIDL_HISTORY_LIMIT:]

    if last_uidl:
        metadata["pop3_last_uidl"] = last_uidl
        metadata["pop3_seen_uidls"] = seen_uidls_raw
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
        except Exception:
            logger.exception(
                "EMAIL_POLL_EXIT reason=auth_or_connection_failure protocol=imap connector_id=%s",
                config.id,
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
        except Exception:
            logger.exception(
                "EMAIL_POLL_EXIT reason=auth_or_connection_failure protocol=pop3 connector_id=%s",
                config.id,
            )
            raise
    return {"processed": processed}
