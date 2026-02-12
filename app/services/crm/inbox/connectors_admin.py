"""Admin connector helpers for CRM inbox."""

from __future__ import annotations

import imaplib
import json
import logging
import poplib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.connector import ConnectorType
from app.schemas.connector import ConnectorConfigUpdate
from app.schemas.integration import IntegrationTargetUpdate
from app.services import connector as connector_service
from app.services import email as email_service
from app.services import integration as integration_service
from app.services.crm import inbox as inbox_service
from app.services.crm.inbox.inboxes import (
    build_email_state_for_target,
    build_whatsapp_state_for_target,
    get_email_channel_state,
    get_whatsapp_channel_state,
)
from app.services.crm.inbox.permissions import can_manage_inbox_settings
from app.services.crm.inbox.targets import resolve_target_and_config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConnectorSaveResult:
    ok: bool
    next_url: str
    query_key: str
    error_detail: str | None = None


def _as_str(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _as_bool(value: object | None) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on"}


def _as_int(value: object | None, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _safe_log_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=True, default=str, sort_keys=True)


def _resolve_email_auth(
    email_channel: dict | None,
    username: str | None,
    password: str | None,
) -> tuple[str | None, str | None]:
    smtp_username = username or None
    smtp_password = password or None
    if not smtp_username and email_channel:
        auth = email_channel.get("auth_config") if isinstance(email_channel, dict) else None
        if isinstance(auth, dict):
            smtp_username = auth.get("username") or smtp_username
            smtp_password = auth.get("password") or smtp_password
    return smtp_username, smtp_password


def _test_imap_connection(
    host: str,
    port: int,
    use_ssl: bool,
    username: str,
    password: str,
    mailbox: str | None,
) -> tuple[bool, str | None]:
    client = None
    try:
        client = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
        client.login(username, password)
        status, _ = client.select(mailbox or "INBOX")
        if status != "OK":
            return False, f"IMAP mailbox select failed ({status})"
        client.logout()
        return True, None
    except Exception as exc:
        return False, f"IMAP test failed: {exc}"
    finally:
        try:
            if client is not None:
                client.logout()
        except Exception:
            logger.debug("IMAP logout failed during disconnect.", exc_info=True)


def _test_pop3_connection(
    host: str,
    port: int,
    use_ssl: bool,
    username: str,
    password: str,
) -> tuple[bool, str | None]:
    client = None
    try:
        client = poplib.POP3_SSL(host, port) if use_ssl else poplib.POP3(host, port)
        client.user(username)
        client.pass_(password)
        client.stat()
        client.quit()
        return True, None
    except Exception as exc:
        return False, f"POP3 test failed: {exc}"
    finally:
        try:
            if client is not None:
                client.quit()
        except Exception:
            logger.debug("SMTP quit failed during disconnect.", exc_info=True)


def _normalize_next_url(next_url: str | None, default_url: str) -> str:
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        return default_url
    return next_url


def configure_email_connector(
    db: Session,
    *,
    form: Mapping[str, Any],
    defaults: Mapping[str, Any],
    next_url: str | None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> ConnectorSaveResult:
    if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
        return ConnectorSaveResult(
            ok=False,
            next_url=_normalize_next_url(next_url, "/admin/crm/inbox/settings"),
            query_key="email_error",
            error_detail="Forbidden",
        )
    target_id_value = _as_str(form.get("target_id")) if "target_id" in form else _as_str(defaults.get("target_id"))
    connector_id_value = (
        _as_str(form.get("connector_id")) if "connector_id" in form else _as_str(defaults.get("connector_id"))
    )
    create_new_value = _as_str(form.get("create_new")) if "create_new" in form else _as_str(defaults.get("create_new"))
    create_new_flag = _as_bool(create_new_value)
    polling_enabled_values = (
        form.getlist("polling_enabled") if hasattr(form, "getlist") and "polling_enabled" in form else []
    )
    polling_enabled_value = (
        _as_str(polling_enabled_values[-1]) if polling_enabled_values else _as_str(defaults.get("polling_enabled"))
    )

    smtp_port_value = _as_str(form.get("smtp_port")) if "smtp_port" in form else _as_str(defaults.get("smtp_port"))
    imap_port_value = _as_str(form.get("imap_port")) if "imap_port" in form else _as_str(defaults.get("imap_port"))
    pop3_port_value = _as_str(form.get("pop3_port")) if "pop3_port" in form else _as_str(defaults.get("pop3_port"))
    smtp_host_value = _as_str(form.get("smtp_host")) if "smtp_host" in form else _as_str(defaults.get("smtp_host"))
    imap_host_value = _as_str(form.get("imap_host")) if "imap_host" in form else _as_str(defaults.get("imap_host"))
    pop3_host_value = _as_str(form.get("pop3_host")) if "pop3_host" in form else _as_str(defaults.get("pop3_host"))
    imap_search_all_value = _as_bool(
        _as_str(form.get("imap_search_all")) if "imap_search_all" in form else _as_str(defaults.get("imap_search_all"))
    )
    logger.info(
        "crm_inbox_email_form_ports %s",
        _safe_log_json(
            {
                "smtp_port": _as_str(form.get("smtp_port")),
                "imap_port": _as_str(form.get("imap_port")),
                "pop3_port": _as_str(form.get("pop3_port")),
                "smtp_host": _as_str(form.get("smtp_host")),
                "imap_host": _as_str(form.get("imap_host")),
                "pop3_host": _as_str(form.get("pop3_host")),
            }
        ),
    )
    imap_host_provided = "imap_host" in form
    pop3_host_provided = "pop3_host" in form

    name = _as_str(defaults.get("name")) or "CRM Email"
    username = _as_str(defaults.get("username"))
    password = _as_str(defaults.get("password"))
    from_email = _as_str(defaults.get("from_email"))
    from_name = _as_str(defaults.get("from_name"))
    smtp_use_tls = _as_str(defaults.get("smtp_use_tls"))
    smtp_use_ssl = _as_str(defaults.get("smtp_use_ssl"))
    skip_smtp_test = _as_str(defaults.get("skip_smtp_test"))
    smtp_enabled = _as_str(defaults.get("smtp_enabled"))
    imap_use_ssl = _as_str(defaults.get("imap_use_ssl"))
    imap_mailbox = _as_str(defaults.get("imap_mailbox"))
    pop3_use_ssl = _as_str(defaults.get("pop3_use_ssl"))
    poll_interval_seconds = _as_str(defaults.get("poll_interval_seconds"))
    rate_limit_per_minute_provided = "rate_limit_per_minute" in form
    rate_limit_per_minute_value = (
        _as_str(form.get("rate_limit_per_minute"))
        if rate_limit_per_minute_provided
        else _as_str(defaults.get("rate_limit_per_minute"))
    )

    email_channel = get_email_channel_state(db)
    resolved_target = None
    resolved_config = None
    if not create_new_flag and (target_id_value or connector_id_value):
        resolved_target, resolved_config = resolve_target_and_config(
            db, target_id_value, connector_id_value, ConnectorType.email
        )
        if resolved_target and resolved_config:
            email_channel = build_email_state_for_target(db, resolved_target, resolved_config)
        else:
            raise ValueError("Email connector not found")

    smtp = None
    smtp_on = _as_bool(smtp_enabled) if smtp_enabled is not None else bool(smtp_host_value)
    if smtp_on and smtp_host_value:
        existing_smtp = email_channel.get("smtp") if email_channel else None
        smtp = {
            "host": smtp_host_value.strip(),
            "port": _as_int(
                smtp_port_value,
                existing_smtp.get("port") if isinstance(existing_smtp, dict) else 587,
            ),
            "use_tls": _as_bool(smtp_use_tls)
            if smtp_use_tls is not None
            else bool(existing_smtp.get("use_tls"))
            if isinstance(existing_smtp, dict)
            else False,
            "use_ssl": _as_bool(smtp_use_ssl)
            if smtp_use_ssl is not None
            else bool(existing_smtp.get("use_ssl"))
            if isinstance(existing_smtp, dict)
            else False,
        }

    imap = None
    if imap_host_value:
        existing_imap = email_channel.get("imap") if email_channel else None
        imap = {
            "host": imap_host_value.strip(),
            "port": _as_int(
                imap_port_value,
                existing_imap.get("port") if isinstance(existing_imap, dict) else 993,
            ),
            "use_ssl": _as_bool(imap_use_ssl)
            if imap_use_ssl is not None
            else bool(existing_imap.get("use_ssl"))
            if isinstance(existing_imap, dict)
            else True,
            "mailbox": imap_mailbox.strip()
            if imap_mailbox
            else existing_imap.get("mailbox")
            if isinstance(existing_imap, dict)
            else "INBOX",
        }
        imap["search_all"] = bool(imap_search_all_value)

    pop3 = None
    if pop3_host_value:
        existing_pop3 = email_channel.get("pop3") if email_channel else None
        pop3 = {
            "host": pop3_host_value.strip(),
            "port": _as_int(
                pop3_port_value,
                existing_pop3.get("port") if isinstance(existing_pop3, dict) else 995,
            ),
            "use_ssl": _as_bool(pop3_use_ssl)
            if pop3_use_ssl is not None
            else bool(existing_pop3.get("use_ssl"))
            if isinstance(existing_pop3, dict)
            else True,
        }

    logger.info(
        "crm_inbox_email_save_request %s",
        _safe_log_json(
            {
                "smtp_on": smtp_on,
                "smtp_host": smtp_host_value.strip() if smtp_host_value else None,
                "smtp_port": _as_int(smtp_port_value, 587) if smtp_port_value else None,
                "imap_host": imap_host_value.strip() if imap_host_value else None,
                "imap_port": _as_int(imap_port_value, 993) if imap_port_value else None,
                "pop3_host": pop3_host_value.strip() if pop3_host_value else None,
                "pop3_port": _as_int(pop3_port_value, 995) if pop3_port_value else None,
            }
        ),
    )

    smtp_username, smtp_password = _resolve_email_auth(email_channel, username, password)
    try:
        if smtp and not _as_bool(skip_smtp_test):
            if not smtp_username or not smtp_password:
                raise ValueError("SMTP credentials are required to validate the connection.")
            smtp_test_config = dict(smtp)
            smtp_test_config["username"] = smtp_username
            smtp_test_config["password"] = smtp_password
            ok, error = email_service.test_smtp_connection(smtp_test_config)
            if not ok:
                raise ValueError(error or "SMTP test failed")

        if imap:
            if not smtp_username or not smtp_password:
                raise ValueError("IMAP credentials are required to validate the connection.")
            mailbox = imap.get("mailbox") if isinstance(imap, dict) else None
            imap_host = str(imap.get("host") or "").strip() if isinstance(imap, dict) else ""
            imap_port_value = imap.get("port") if isinstance(imap, dict) else None
            if not imap_host or imap_port_value is None:
                raise ValueError("IMAP host and port are required to validate the connection.")
            if isinstance(imap_port_value, int | str):
                imap_port_num = int(imap_port_value)
            else:
                raise ValueError("IMAP port must be a number.")
            ok, error = _test_imap_connection(
                imap_host,
                imap_port_num,
                bool(imap.get("use_ssl")),
                smtp_username,
                smtp_password,
                mailbox or "INBOX",
            )
            if not ok:
                raise ValueError(error or "IMAP test failed")

        if pop3:
            if not smtp_username or not smtp_password:
                raise ValueError("POP3 credentials are required to validate the connection.")
            pop3_host = str(pop3.get("host") or "").strip() if isinstance(pop3, dict) else ""
            pop3_config_port_value = pop3.get("port") if isinstance(pop3, dict) else None
            if not pop3_host or pop3_config_port_value is None:
                raise ValueError("POP3 host and port are required to validate the connection.")
            if isinstance(pop3_config_port_value, int | str):
                pop3_port_num = int(pop3_config_port_value)
            else:
                raise ValueError("POP3 port must be a number.")
            ok, error = _test_pop3_connection(
                pop3_host,
                pop3_port_num,
                bool(pop3.get("use_ssl")),
                smtp_username,
                smtp_password,
            )
            if not ok:
                raise ValueError(error or "POP3 test failed")
    except Exception as exc:
        return ConnectorSaveResult(
            ok=False,
            next_url=_normalize_next_url(next_url, "/admin/crm/inbox"),
            query_key="email_error",
            error_detail=str(exc) or "Email validation failed",
        )

    try:
        if not create_new_flag and email_channel and email_channel.get("connector_id"):
            config = connector_service.connector_configs.get(db, email_channel["connector_id"])
            metadata = dict(config.metadata_ or {}) if isinstance(config.metadata_, dict) else {}
            if smtp_on and smtp:
                metadata["smtp"] = smtp
            elif smtp_enabled is not None and not smtp_on:
                metadata.pop("smtp", None)

            if imap:
                metadata["imap"] = imap
            elif imap_host_provided and not imap_host_value:
                metadata.pop("imap", None)

            if pop3:
                metadata["pop3"] = pop3
            elif pop3_host_provided and not pop3_host_value:
                metadata.pop("pop3", None)
            if rate_limit_per_minute_value:
                metadata["rate_limit_per_minute"] = _as_int(rate_limit_per_minute_value)
            elif rate_limit_per_minute_provided:
                metadata.pop("rate_limit_per_minute", None)

            auth_config = dict(config.auth_config or {}) if isinstance(config.auth_config, dict) else {}
            if username:
                auth_config["username"] = username.strip()
            if password:
                auth_config["password"] = password
            if from_email:
                auth_config["from_email"] = from_email.strip()
            if from_name:
                auth_config["from_name"] = from_name.strip()

            connector_service.connector_configs.update(
                db,
                email_channel["connector_id"],
                ConnectorConfigUpdate(
                    name=name.strip() if name else config.name,
                    connector_type=ConnectorType.email,
                    auth_config=auth_config,
                    metadata_=metadata,
                    is_active=True,
                ),
            )
            integration_service.integration_targets.update(
                db,
                email_channel["target_id"],
                IntegrationTargetUpdate(name=name.strip() if name else None, is_active=True),
            )
            target_id = email_channel["target_id"]
        else:
            auth_config = {}
            if username:
                auth_config["username"] = username.strip()
            if password:
                auth_config["password"] = password
            if from_email:
                auth_config["from_email"] = from_email.strip()
            if from_name:
                auth_config["from_name"] = from_name.strip()
            metadata = {}
            if smtp_on and smtp:
                metadata["smtp"] = smtp
            if imap:
                metadata["imap"] = imap
            if pop3:
                metadata["pop3"] = pop3
            if rate_limit_per_minute_value:
                metadata["rate_limit_per_minute"] = _as_int(rate_limit_per_minute_value)
            elif rate_limit_per_minute_provided:
                metadata.pop("rate_limit_per_minute", None)
            target = inbox_service.create_email_connector_target(
                db,
                name=name.strip() if name else "CRM Email",
                smtp=smtp,
                imap=imap,
                pop3=pop3,
                auth_config=auth_config or None,
                metadata={"rate_limit_per_minute": _as_int(rate_limit_per_minute_value)}
                if rate_limit_per_minute_value
                else None,
            )
            target_id = str(target.id)

        interval_seconds = _as_int(poll_interval_seconds)
        polling_flag_set = bool(polling_enabled_values) or polling_enabled_value is not None
        polling_on = (
            _as_bool(polling_enabled_value)
            if polling_flag_set
            else bool(interval_seconds)
            or bool(email_channel and email_channel.get("polling_active"))
            or bool(email_channel and email_channel.get("poll_interval_seconds"))
        )
        if polling_on and not interval_seconds:
            interval_seconds = (email_channel.get("poll_interval_seconds") if email_channel else None) or 300
        if polling_flag_set and not polling_on:
            integration_service.IntegrationJobs.disable_import_jobs_for_target(db, target_id)
        elif polling_on and (imap or pop3):
            inbox_service.ensure_email_polling_job(
                db,
                target_id=target_id,
                interval_seconds=interval_seconds or 300,
                name=f"{name.strip() if name else 'CRM Email'} Polling",
            )
    except Exception:
        return ConnectorSaveResult(
            ok=False,
            next_url=_normalize_next_url(next_url, "/admin/crm/inbox"),
            query_key="email_error",
            error_detail=None,
        )

    return ConnectorSaveResult(
        ok=True,
        next_url=_normalize_next_url(next_url, "/admin/crm/inbox"),
        query_key="email_setup",
        error_detail=None,
    )


def configure_whatsapp_connector(
    db: Session,
    *,
    form: Mapping[str, Any],
    defaults: Mapping[str, Any],
    next_url: str | None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> ConnectorSaveResult:
    if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
        return ConnectorSaveResult(
            ok=False,
            next_url=_normalize_next_url(next_url, "/admin/crm/inbox/settings"),
            query_key="whatsapp_error",
            error_detail="Forbidden",
        )
    target_id_value = _as_str(form.get("target_id")) if "target_id" in form else _as_str(defaults.get("target_id"))
    connector_id_value = (
        _as_str(form.get("connector_id")) if "connector_id" in form else _as_str(defaults.get("connector_id"))
    )
    create_new_value = _as_str(form.get("create_new")) if "create_new" in form else _as_str(defaults.get("create_new"))
    create_new_flag = _as_bool(create_new_value)

    name = _as_str(defaults.get("name")) or "CRM WhatsApp"
    access_token = _as_str(defaults.get("access_token"))
    phone_number_id = _as_str(defaults.get("phone_number_id"))
    business_account_id = _as_str(defaults.get("business_account_id"))
    base_url = _as_str(defaults.get("base_url"))
    rate_limit_per_minute_provided = "rate_limit_per_minute" in form
    rate_limit_per_minute_value = (
        _as_str(form.get("rate_limit_per_minute"))
        if rate_limit_per_minute_provided
        else _as_str(defaults.get("rate_limit_per_minute"))
    )

    whatsapp_channel = get_whatsapp_channel_state(db)
    if not create_new_flag and (target_id_value or connector_id_value):
        resolved_target, resolved_config = resolve_target_and_config(
            db, target_id_value, connector_id_value, ConnectorType.whatsapp
        )
        if resolved_target and resolved_config:
            whatsapp_channel = build_whatsapp_state_for_target(resolved_target, resolved_config)
        else:
            raise ValueError("WhatsApp connector not found")

    try:
        if not create_new_flag and whatsapp_channel and whatsapp_channel.get("connector_id"):
            config = connector_service.connector_configs.get(db, whatsapp_channel["connector_id"])
            merged_metadata = dict(config.metadata_ or {}) if isinstance(config.metadata_, dict) else {}
            if phone_number_id:
                merged_metadata["phone_number_id"] = phone_number_id.strip()
            if business_account_id:
                merged_metadata["business_account_id"] = business_account_id.strip()
            if rate_limit_per_minute_value:
                merged_metadata["rate_limit_per_minute"] = _as_int(rate_limit_per_minute_value)
            elif rate_limit_per_minute_provided:
                merged_metadata.pop("rate_limit_per_minute", None)
            auth_config = dict(config.auth_config or {}) if isinstance(config.auth_config, dict) else {}
            if access_token:
                auth_config["access_token"] = access_token.strip()

            update_payload = ConnectorConfigUpdate(
                name=name.strip() if name else None,
                connector_type=ConnectorType.whatsapp,
                auth_config=auth_config,
                base_url=base_url.strip() if base_url else config.base_url,
                metadata_=merged_metadata or None,
            )
            connector_service.connector_configs.update(
                db,
                whatsapp_channel["connector_id"],
                update_payload,
            )
            integration_service.integration_targets.update(
                db,
                whatsapp_channel["target_id"],
                IntegrationTargetUpdate(name=name.strip() if name else None),
            )
        else:
            auth_config = None
            if access_token:
                auth_config = {"access_token": access_token.strip()}
            inbox_service.create_whatsapp_connector_target(
                db,
                name=name.strip() if name else "CRM WhatsApp",
                phone_number_id=phone_number_id.strip() if phone_number_id else None,
                auth_config=auth_config,
                base_url=base_url.strip() if base_url else None,
                metadata={
                    **({"business_account_id": business_account_id.strip()} if business_account_id else {}),
                    **(
                        {"rate_limit_per_minute": _as_int(rate_limit_per_minute_value)}
                        if rate_limit_per_minute_value
                        else {}
                    ),
                }
                if (business_account_id or rate_limit_per_minute_value)
                else None,
            )
    except Exception:
        return ConnectorSaveResult(
            ok=False,
            next_url=_normalize_next_url(next_url, "/admin/crm/inbox/settings"),
            query_key="whatsapp_error",
            error_detail=None,
        )

    return ConnectorSaveResult(
        ok=True,
        next_url=_normalize_next_url(next_url, "/admin/crm/inbox/settings"),
        query_key="whatsapp_setup",
        error_detail=None,
    )
