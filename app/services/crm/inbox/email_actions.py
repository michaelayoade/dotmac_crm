"""Email inbox action helpers for CRM inbox."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from html import escape as html_escape

from sqlalchemy.orm import Session

from app.models.connector import ConnectorType
from app.schemas.connector import ConnectorConfigUpdate
from app.schemas.integration import IntegrationTargetUpdate
from app.services import connector as connector_service
from app.services import email as email_service
from app.services import integration as integration_service
from app.services.crm import inbox as inbox_service
from app.services.crm.inbox.inboxes import get_email_channel_state
from app.services.crm.inbox.permissions import can_manage_inbox_settings
from app.services.crm.inbox.targets import resolve_target_and_config


@dataclass(frozen=True)
class HtmlResult:
    status_code: int
    message: str


@dataclass(frozen=True)
class RedirectResult:
    ok: bool
    next_url: str
    query_key: str
    error_detail: str | None = None


def poll_email_channel(
    db: Session,
    *,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> HtmlResult:
    if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
        return HtmlResult(
            status_code=403,
            message="<p class='text-xs text-red-400'>Forbidden</p>",
        )
    try:
        result = inbox_service.poll_email_targets(db)
        processed = int(result.get("processed") or 0)
        return HtmlResult(
            status_code=200,
            message=f"<p class='text-xs text-emerald-400'>Checked inbox: {processed} new message(s).</p>",
        )
    except Exception as exc:
        return HtmlResult(
            status_code=400,
            message=f"<p class='text-xs text-red-400'>Email poll failed: {exc}</p>",
        )


def check_email_inbox(
    db: Session,
    target_id: str | None,
    *,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> HtmlResult:
    if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
        return HtmlResult(
            status_code=403,
            message="<p class='text-xs text-red-400'>Forbidden</p>",
        )
    target_id_value = (target_id or "").strip()
    if not target_id_value:
        return HtmlResult(
            status_code=400,
            message="<p class='text-xs text-red-400'>Select an inbox to check.</p>",
        )

    try:
        resolved_target, resolved_config = resolve_target_and_config(db, target_id_value, None, ConnectorType.email)
    except Exception as exc:
        return HtmlResult(
            status_code=400,
            message=f"<p class='text-xs text-red-400'>Inbox lookup failed: {html_escape(str(exc))}</p>",
        )

    if not resolved_target or not resolved_config:
        return HtmlResult(
            status_code=404,
            message="<p class='text-xs text-red-400'>Email inbox not found.</p>",
        )

    poll_detail = None
    try:
        poll_result = inbox_service.poll_email_targets(db, target_id=str(resolved_target.id))
        processed = int(poll_result.get("processed") or 0)
        poll_detail = f"Checked inbox: {processed} new message(s)."
    except Exception as exc:
        poll_detail = f"Polling failed: {exc}"

    smtp_detail = None
    smtp_config = inbox_service._smtp_config_from_connector(resolved_config)
    if smtp_config:
        recipient = None
        auth_config = resolved_config.auth_config if isinstance(resolved_config.auth_config, dict) else {}
        if isinstance(auth_config.get("from_email"), str):
            recipient = auth_config.get("from_email")
        if not recipient and isinstance(auth_config.get("username"), str):
            recipient = auth_config.get("username")
        if not recipient and isinstance(smtp_config.get("from_email"), str):
            recipient = smtp_config.get("from_email")
        if recipient:
            sent = email_service.send_email_with_config(
                smtp_config,
                recipient,
                "CRM inbox test email",
                "This is a test email from the CRM inbox settings check.",
                "This is a test email from the CRM inbox settings check.",
            )
            smtp_detail = f"Test email sent to {recipient}." if sent else "SMTP send failed."
        else:
            smtp_detail = "SMTP test skipped: no from email configured."
    else:
        smtp_detail = "SMTP test skipped: SMTP config missing."

    message = " ".join(part for part in (poll_detail, smtp_detail) if part)
    if "failed" in (poll_detail or "").lower() or "failed" in (smtp_detail or "").lower():
        return HtmlResult(
            status_code=400,
            message=f"<p class='text-xs text-red-400'>{html_escape(message)}</p>",
        )
    return HtmlResult(
        status_code=200,
        message=f"<p class='text-xs text-emerald-400'>{html_escape(message)}</p>",
    )


def reset_email_imap_cursor(
    db: Session,
    target_id: str | None,
    *,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> HtmlResult:
    if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
        return HtmlResult(
            status_code=403,
            message="<p class='text-xs text-red-400'>Forbidden</p>",
        )
    target_id_value = (target_id or "").strip()
    if not target_id_value:
        return HtmlResult(
            status_code=400,
            message="<p class='text-xs text-red-400'>Select an inbox to reset.</p>",
        )
    try:
        _, resolved_config = resolve_target_and_config(db, target_id_value, None, ConnectorType.email)
    except Exception as exc:
        return HtmlResult(
            status_code=400,
            message=f"<p class='text-xs text-red-400'>Inbox lookup failed: {html_escape(str(exc))}</p>",
        )
    if not resolved_config:
        return HtmlResult(
            status_code=404,
            message="<p class='text-xs text-red-400'>Email inbox not found.</p>",
        )
    metadata = resolved_config.metadata_ if isinstance(resolved_config.metadata_, dict) else {}
    metadata.pop("imap_last_uid", None)
    metadata.pop("imap_last_uid_by_mailbox", None)
    resolved_config.metadata_ = metadata
    db.commit()
    return HtmlResult(
        status_code=200,
        message="<p class='text-xs text-emerald-400'>IMAP cursor reset. Next poll will re-scan unseen mail.</p>",
    )


def reset_email_polling_runs(
    db: Session,
    target_id: str | None,
    next_url: str | None,
    *,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> RedirectResult:
    if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
        return RedirectResult(
            ok=False,
            next_url=_normalize_next_url(next_url),
            query_key="email_error",
            error_detail="Forbidden",
        )
    target_id_value = (target_id or "").strip()
    if target_id_value:
        integration_service.reset_stuck_runs(db, target_id_value)
    else:
        email_channel = get_email_channel_state(db)
        if email_channel and email_channel.get("target_id"):
            integration_service.reset_stuck_runs(db, email_channel["target_id"])
    next_value = next_url or "/admin/crm/inbox/settings"
    if not next_value.startswith("/") or next_value.startswith("//"):
        next_value = "/admin/crm/inbox/settings"
    return RedirectResult(ok=True, next_url=next_value, query_key="email_setup")


def delete_email_inbox(
    db: Session,
    target_id: str | None,
    connector_id: str | None,
    next_url: str | None,
    *,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> RedirectResult:
    if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
        return RedirectResult(
            ok=False,
            next_url=_normalize_next_url(next_url),
            query_key="email_error",
            error_detail="Forbidden",
        )
    target_id_value = (target_id or "").strip()
    if not target_id_value:
        return RedirectResult(
            ok=False,
            next_url=_normalize_next_url(next_url),
            query_key="email_error",
            error_detail="Inbox target is required.",
        )

    with contextlib.suppress(Exception):
        integration_service.IntegrationJobs.disable_import_jobs_for_target(db, target_id_value)

    try:
        integration_service.integration_targets.delete(db, target_id_value)
    except Exception as exc:
        return RedirectResult(
            ok=False,
            next_url=_normalize_next_url(next_url),
            query_key="email_error",
            error_detail=str(exc) or "Failed to delete inbox",
        )

    connector_id_value = (connector_id or "").strip()
    if connector_id_value:
        with contextlib.suppress(Exception):
            connector_service.connector_configs.delete(db, connector_id_value)

    return RedirectResult(ok=True, next_url=_normalize_next_url(next_url), query_key="email_setup")


def activate_email_inbox(
    db: Session,
    target_id: str | None,
    connector_id: str | None,
    next_url: str | None,
    *,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> RedirectResult:
    if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
        return RedirectResult(
            ok=False,
            next_url=_normalize_next_url(next_url),
            query_key="email_error",
            error_detail="Forbidden",
        )
    target_id_value = (target_id or "").strip()
    if not target_id_value:
        return RedirectResult(
            ok=False,
            next_url=_normalize_next_url(next_url),
            query_key="email_error",
            error_detail="Inbox target is required.",
        )
    try:
        integration_service.integration_targets.update(
            db,
            target_id_value,
            IntegrationTargetUpdate(is_active=True),
        )
    except Exception as exc:
        return RedirectResult(
            ok=False,
            next_url=_normalize_next_url(next_url),
            query_key="email_error",
            error_detail=str(exc) or "Failed to activate inbox",
        )

    connector_id_value = (connector_id or "").strip()
    if connector_id_value:
        with contextlib.suppress(Exception):
            connector_service.connector_configs.update(
                db,
                connector_id_value,
                ConnectorConfigUpdate(is_active=True),
            )

    return RedirectResult(ok=True, next_url=_normalize_next_url(next_url), query_key="email_setup")


def _normalize_next_url(next_url: str | None) -> str:
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        return "/admin/crm/inbox/settings"
    return next_url
