"""WhatsApp call control actions and WebRTC helper utilities."""

from __future__ import annotations

import re
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.logging import get_logger
from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.conversation import Message
from app.models.crm.enums import ChannelType
from app.models.domain_settings import SettingDomain
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.models.person import Person
from app.schemas.crm.inbox import WhatsAppCallActionRequest
from app.services.common import coerce_uuid
from app.services.crm.inbox.errors import InboxAuthError, InboxConfigError, InboxExternalError, InboxNotFoundError
from app.services.settings_spec import resolve_value

logger = get_logger(__name__)

_CALL_ACTIONS_REQUIRE_SESSION = {"connect", "pre_accept", "accept"}
_VALID_CALL_ACTIONS = {"connect", "pre_accept", "accept", "reject", "terminate"}


def _coerce_positive_int(value: object, default: int) -> int:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        if parsed > 0:
            return parsed
    return default


def _normalize_action(raw_action: str) -> str:
    normalized = (raw_action or "").strip().replace("-", "_").lower()
    if normalized not in _VALID_CALL_ACTIONS:
        raise InboxConfigError(
            "whatsapp_call_invalid_action",
            f"Unsupported call action '{raw_action}'.",
        )
    return normalized


def _normalize_phone_number_id(raw_phone_number_id: str | None) -> str | None:
    if not raw_phone_number_id:
        return None
    phone_number_id = str(raw_phone_number_id).strip()
    return phone_number_id or None


def _resolve_target_query(db: Session):
    return (
        db.query(IntegrationTarget)
        .join(ConnectorConfig, ConnectorConfig.id == IntegrationTarget.connector_config_id)
        .filter(IntegrationTarget.target_type == IntegrationTargetType.crm)
        .filter(IntegrationTarget.is_active.is_(True))
        .filter(ConnectorConfig.connector_type == ConnectorType.whatsapp)
        .filter(ConnectorConfig.is_active.is_(True))
    )


def _resolve_webrtc_action_target(
    db: Session,
    target_id: str | None,
    phone_number_id: str | None,
) -> IntegrationTarget:
    query = _resolve_target_query(db)

    if target_id:
        try:
            target_uuid = coerce_uuid(target_id)
        except Exception as exc:
            raise InboxConfigError(
                "whatsapp_call_invalid_target",
                "Invalid WhatsApp integration target id.",
            ) from exc
        target = query.filter(IntegrationTarget.id == target_uuid).first()
        if not target:
            raise InboxNotFoundError("whatsapp_call_target_not_found", "WhatsApp integration target not found")
        return target

    phone_number_id = _normalize_phone_number_id(phone_number_id)
    if phone_number_id:
        for candidate in query.order_by(IntegrationTarget.created_at.desc()).all():
            config = candidate.connector_config
            if not isinstance(config, ConnectorConfig) or not isinstance(config.metadata_, dict):
                continue
            metadata = config.metadata_
            auth_config = config.auth_config if isinstance(config.auth_config, dict) else {}
            candidate_phone_number_id = metadata.get("phone_number_id") or auth_config.get("phone_number_id")
            if candidate_phone_number_id and str(candidate_phone_number_id) == str(phone_number_id):
                return candidate

    target = query.order_by(IntegrationTarget.created_at.desc()).first()
    if not target:
        raise InboxNotFoundError("whatsapp_call_target_not_found", "No active WhatsApp integration target found")
    return target


def _resolve_target_config(target: IntegrationTarget) -> ConnectorConfig:
    config = target.connector_config
    if not isinstance(config, ConnectorConfig):
        raise InboxConfigError(
            "whatsapp_call_target_invalid", "WhatsApp integration target is missing connector config"
        )
    return config


def _resolve_access_token(config: ConnectorConfig) -> str:
    auth = config.auth_config if isinstance(config.auth_config, dict) else {}
    token = auth.get("token") or auth.get("access_token")
    if not token:
        raise InboxAuthError("whatsapp_call_token_missing", "WhatsApp access token missing for call action")
    return str(token)


def _resolve_phone_number_id(config: ConnectorConfig) -> str | None:
    if isinstance(config.metadata_, dict):
        metadata_phone_number_id = config.metadata_.get("phone_number_id")
        if metadata_phone_number_id:
            return str(metadata_phone_number_id)
    if isinstance(config.auth_config, dict):
        auth_phone_number_id = config.auth_config.get("phone_number_id")
        if auth_phone_number_id:
            return str(auth_phone_number_id)
    return None


def _resolve_call_session(payload: WhatsAppCallActionRequest) -> dict[str, str] | None:
    if payload.session is not None:
        if not isinstance(payload.session, dict):
            raise InboxConfigError("whatsapp_call_session_invalid", "session must be an object")
        sdp = payload.session.get("sdp")
        sdp_type = payload.session.get("sdp_type")
    else:
        sdp = payload.sdp
        sdp_type = payload.sdp_type

    if sdp is None and sdp_type is None:
        return None

    if not isinstance(sdp_type, str) or not sdp_type.strip():
        raise InboxConfigError("whatsapp_call_session_invalid", "sdp_type is required when session is provided")
    if not isinstance(sdp, str) or not sdp.strip():
        raise InboxConfigError("whatsapp_call_session_invalid", "sdp is required when session is provided")

    return {
        "sdp_type": sdp_type.strip(),
        "sdp": sdp,
    }


def _normalize_sdp_for_whatsapp(session_payload: dict[str, str] | None) -> dict[str, str] | None:
    if not session_payload:
        return session_payload

    sdp_type = (session_payload.get("sdp_type") or "").strip().lower()
    sdp = session_payload.get("sdp")
    if not sdp_type or not isinstance(sdp, str) or not sdp.strip():
        return session_payload

    normalized_sdp = sdp.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized_sdp.split("\n")
    normalized_lines: list[str] = []
    for line in lines:
        if not line:
            continue
        lowered = line.lower()
        fingerprint_match = re.match(r"^a=fingerprint:([A-Za-z0-9-]+)\s+(.+)$", line)
        if fingerprint_match:
            algo = fingerprint_match.group(1).upper()
            value = fingerprint_match.group(2).strip()
            if algo != "SHA-256":
                continue
            normalized_lines.append(f"a=fingerprint:{algo} {value}")
            continue
        # Keep TURN relay TCP candidates for fallback paths, but drop non-relay TCP
        # candidates that are commonly useless/noisy in provider-side validation.
        if lowered.startswith("a=candidate:") and " tcp " in lowered:
            candidate_type_match = re.search(r"\styp\s([a-z0-9_]+)", lowered)
            candidate_type = candidate_type_match.group(1) if candidate_type_match else ""
            if candidate_type != "relay":
                continue
        # Trickle is not required for posted SDP answers and can trip provider validation.
        if sdp_type == "answer" and lowered == "a=ice-options:trickle":
            continue
        if sdp_type == "answer" and line == "a=setup:actpass":
            normalized_lines.append("a=setup:active")
            continue
        normalized_lines.append(line)

    normalized = "\r\n".join(normalized_lines).strip()
    if normalized and not normalized.endswith("\r\n"):
        normalized = f"{normalized}\r\n"
    return {
        "sdp_type": sdp_type,
        "sdp": normalized or sdp,
    }


def _normalize_embedded_session(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    sdp_type = value.get("sdp_type")
    sdp = value.get("sdp")
    if not isinstance(sdp_type, str) or not sdp_type.strip():
        return None
    if not isinstance(sdp, str) or not sdp.strip():
        return None
    return {
        "sdp_type": sdp_type.strip(),
        "sdp": sdp,
    }


def _extract_call_context_from_message(message: Message, call_id: str) -> dict[str, Any] | None:
    metadata: dict[str, Any] = message.metadata_ if isinstance(message.metadata_, dict) else {}
    raw_call_value = metadata.get("call")
    raw_call: dict[str, Any] = raw_call_value if isinstance(raw_call_value, dict) else {}

    meta_call_id = metadata.get("call_id")
    raw_call_id = raw_call.get("call_id") or raw_call.get("id")
    resolved_call_id = call_id
    if isinstance(meta_call_id, str) and meta_call_id.strip():
        resolved_call_id = meta_call_id.strip()
    elif isinstance(raw_call_id, str) and raw_call_id.strip():
        resolved_call_id = raw_call_id.strip()

    session_payload = _normalize_embedded_session(raw_call.get("session"))
    if session_payload is None:
        session_payload = _normalize_embedded_session(metadata.get("session"))

    phone_number_id = metadata.get("phone_number_id")
    display_phone_number = metadata.get("display_phone_number")
    call_status = (
        metadata.get("call_status") or raw_call.get("call_status") or raw_call.get("event") or raw_call.get("status")
    )
    call_direction = metadata.get("call_direction") or raw_call.get("call_direction") or raw_call.get("direction")
    call_to = metadata.get("to") or raw_call.get("to")
    call_from = metadata.get("from") or raw_call.get("from")

    return {
        "call_id": resolved_call_id,
        "phone_number_id": str(phone_number_id).strip()
        if isinstance(phone_number_id, str) and phone_number_id.strip()
        else None,
        "display_phone_number": (
            str(display_phone_number).strip()
            if isinstance(display_phone_number, str) and display_phone_number.strip()
            else None
        ),
        "call_status": str(call_status).strip() if isinstance(call_status, str) and call_status.strip() else None,
        "call_direction": (
            str(call_direction).strip() if isinstance(call_direction, str) and call_direction.strip() else None
        ),
        "to": str(call_to).strip() if isinstance(call_to, str) and call_to.strip() else None,
        "from": str(call_from).strip() if isinstance(call_from, str) and call_from.strip() else None,
        "session": session_payload,
    }


def get_whatsapp_call_context(db: Session, call_id: str) -> dict[str, Any]:
    normalized_call_id = (call_id or "").strip()
    if not normalized_call_id:
        raise InboxConfigError("whatsapp_call_id_missing", "Call id is required.")

    # Prefer metadata-linked call events and select the newest lifecycle state.
    # This avoids stale contexts where an older "connect" row is returned after
    # a newer "terminate" event for the same call id.
    message = None
    candidates = (
        db.query(Message)
        .filter(Message.channel_type == ChannelType.whatsapp)
        .filter(Message.metadata_.isnot(None))
        .order_by(Message.received_at.desc().nullslast(), Message.created_at.desc())
        .limit(250)
        .all()
    )
    for candidate in candidates:
        metadata: dict[str, Any] = candidate.metadata_ if isinstance(candidate.metadata_, dict) else {}
        candidate_call_id = metadata.get("call_id")
        raw_call_value = metadata.get("call")
        raw_call: dict[str, Any] = raw_call_value if isinstance(raw_call_value, dict) else {}
        nested_call_id = raw_call.get("call_id") or raw_call.get("id")
        if candidate_call_id == normalized_call_id or nested_call_id == normalized_call_id:
            message = candidate
            break

    # Fallback to external id lookup for older rows lacking normalized metadata.
    if not message:
        message = (
            db.query(Message)
            .filter(Message.channel_type == ChannelType.whatsapp)
            .filter(Message.external_id == normalized_call_id)
            .order_by(Message.received_at.desc().nullslast(), Message.created_at.desc())
            .first()
        )

    if not message:
        raise InboxNotFoundError("whatsapp_call_not_found", "WhatsApp call context not found.")

    context = _extract_call_context_from_message(message, normalized_call_id)
    if context is None:
        raise InboxNotFoundError("whatsapp_call_not_found", "WhatsApp call context not found.")
    return context


def _meta_graph_base_url(db: Session) -> str:
    version = resolve_value(db, SettingDomain.comms, "meta_graph_api_version")
    if not version:
        version = settings.meta_graph_api_version
    return f"https://graph.facebook.com/{version}"


def _post_whatsapp_call_action(
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
) -> tuple[int, Any]:
    response = httpx.post(endpoint, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {"raw": response.text}


def _extract_http_error_payload(exc: httpx.HTTPError) -> tuple[int | None, dict[str, Any] | None, str | None]:
    response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
    if response is None:
        return None, None, None
    parsed: dict[str, Any] | None = None
    try:
        raw = response.json()
        parsed = raw if isinstance(raw, dict) else None
    except Exception:
        parsed = None
    return response.status_code, parsed, response.text


def _is_sdp_validation_error(payload: dict[str, Any] | None, body_text: str | None) -> bool:
    error_obj = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error_obj, dict):
        code = error_obj.get("code")
        if code == 138008:
            return True
        message = str(error_obj.get("message") or "").lower()
        user_msg = str(error_obj.get("error_user_msg") or "").lower()
        if "sdp" in message or "sdp" in user_msg:
            return True
    body = (body_text or "").lower()
    return "sdp" in body and ("validation" in body or "invalid" in body)


def _resolve_actor_name(db: Session, actor_person_id: str | None) -> str | None:
    normalized_id = (actor_person_id or "").strip()
    if not normalized_id:
        return None
    try:
        person = db.get(Person, coerce_uuid(normalized_id))
    except Exception:
        return None
    if not isinstance(person, Person):
        return None
    if isinstance(person.display_name, str) and person.display_name.strip():
        return person.display_name.strip()
    full_name = f"{person.first_name or ''} {person.last_name or ''}".strip()
    if full_name:
        return full_name
    if isinstance(person.email, str) and person.email.strip():
        return person.email.strip()
    return None


def _stamp_call_accept_metadata(
    db: Session,
    call_id: str,
    actor_person_id: str | None,
) -> None:
    normalized_call_id = (call_id or "").strip()
    normalized_actor_id = (actor_person_id or "").strip()
    if not normalized_call_id or not normalized_actor_id:
        return

    actor_name = _resolve_actor_name(db, normalized_actor_id)
    candidates = (
        db.query(Message)
        .filter(Message.channel_type == ChannelType.whatsapp)
        .filter(Message.metadata_.isnot(None))
        .order_by(Message.received_at.desc().nullslast(), Message.created_at.desc())
        .limit(250)
        .all()
    )
    updated_any = False
    for candidate in candidates:
        metadata: dict[str, Any] = candidate.metadata_ if isinstance(candidate.metadata_, dict) else {}
        raw_call_value = metadata.get("call")
        raw_call = raw_call_value if isinstance(raw_call_value, dict) else {}
        candidate_call_id = metadata.get("call_id")
        nested_call_id = raw_call.get("call_id") or raw_call.get("id")
        if candidate_call_id != normalized_call_id and nested_call_id != normalized_call_id:
            continue

        updated_metadata = dict(metadata)
        updated_metadata["accepted_by_person_id"] = normalized_actor_id
        if actor_name:
            updated_metadata["accepted_by_name"] = actor_name

        updated_call = dict(raw_call)
        updated_call["accepted_by_person_id"] = normalized_actor_id
        if actor_name:
            updated_call["accepted_by_name"] = actor_name
        if updated_call:
            updated_metadata["call"] = updated_call

        candidate.metadata_ = updated_metadata
        updated_any = True

    if updated_any:
        db.flush()


def perform_whatsapp_call_action(
    db: Session,
    call_id: str,
    payload: WhatsAppCallActionRequest,
    actor_person_id: str | None = None,
) -> dict[str, Any]:
    """Send a WhatsApp call control action."""

    action = _normalize_action(payload.action)
    target = _resolve_webrtc_action_target(
        db, str(payload.target_id) if payload.target_id else None, payload.phone_number_id
    )
    config = _resolve_target_config(target)

    phone_number_id = _normalize_phone_number_id(payload.phone_number_id)
    if not phone_number_id:
        phone_number_id = _resolve_phone_number_id(config)
    if not phone_number_id:
        raise InboxConfigError("whatsapp_call_phone_number_missing", "WhatsApp phone_number_id missing")

    session_payload = _normalize_sdp_for_whatsapp(_resolve_call_session(payload))
    if action in _CALL_ACTIONS_REQUIRE_SESSION and not session_payload:
        raise InboxConfigError(
            "whatsapp_call_session_required",
            f"session (sdp_type + sdp) is required for action '{action}'.",
        )

    base_payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "call_id": call_id,
        "action": action,
    }
    if payload.to:
        base_payload["to"] = payload.to
    if session_payload:
        base_payload["session"] = session_payload

    base_url = config.base_url or _meta_graph_base_url(db)
    token = _resolve_access_token(config)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if isinstance(config.headers, dict):
        headers.update(config.headers)

    endpoint = f"{base_url.rstrip('/')}/{phone_number_id}/calls"
    timeout = _coerce_positive_int(
        resolve_value(db, SettingDomain.comms, "whatsapp_call_api_timeout_seconds"),
        10,
    )

    try:
        response_status: int
        response_json: Any
        if action == "accept" and session_payload and session_payload.get("sdp_type") == "answer":
            pre_accept_payload = dict(base_payload)
            pre_accept_payload["action"] = "pre_accept"
            accept_payload = dict(base_payload)
            accept_payload["action"] = "accept"
            try:
                pre_status, pre_json = _post_whatsapp_call_action(endpoint, headers, pre_accept_payload, timeout)
                response_status, accept_json = _post_whatsapp_call_action(endpoint, headers, accept_payload, timeout)
                response_json = {
                    "flow": "pre_accept_then_accept",
                    "pre_accept": {
                        "status_code": pre_status,
                        "response": pre_json,
                    },
                    "accept": {
                        "status_code": response_status,
                        "response": accept_json,
                    },
                }
            except httpx.HTTPError as primary_exc:
                status_code, error_payload, body_text = _extract_http_error_payload(primary_exc)
                if not _is_sdp_validation_error(error_payload, body_text):
                    raise

                logger.warning(
                    "whatsapp_call_accept_fallback_start call_id=%s status=%s provider_error=%s",
                    call_id,
                    status_code,
                    error_payload or body_text,
                )

                # Fallback 1: accept directly with the same answer session.
                fallback_accept_payload = dict(base_payload)
                fallback_accept_payload["action"] = "accept"
                try:
                    response_status, response_json = _post_whatsapp_call_action(
                        endpoint, headers, fallback_accept_payload, timeout
                    )
                    response_json = {
                        "flow": "accept_only_fallback",
                        "accept": {
                            "status_code": response_status,
                            "response": response_json,
                        },
                    }
                except httpx.HTTPError:
                    # Fallback 2: same accept-only flow, forcing uppercase SDP type.
                    fallback_upper_payload = dict(base_payload)
                    fallback_upper_payload["action"] = "accept"
                    session_obj = fallback_upper_payload.get("session")
                    if isinstance(session_obj, dict):
                        session_obj = dict(session_obj)
                        sdp_type_value = session_obj.get("sdp_type")
                        if isinstance(sdp_type_value, str):
                            session_obj["sdp_type"] = sdp_type_value.upper()
                        fallback_upper_payload["session"] = session_obj
                    response_status, response_json = _post_whatsapp_call_action(
                        endpoint, headers, fallback_upper_payload, timeout
                    )
                    response_json = {
                        "flow": "accept_only_upper_sdp_type_fallback",
                        "accept": {
                            "status_code": response_status,
                            "response": response_json,
                        },
                    }
        else:
            response_status, response_json = _post_whatsapp_call_action(endpoint, headers, base_payload, timeout)
    except httpx.HTTPError as exc:
        response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
        status_code = response.status_code if response is not None else None
        body = response.text if response is not None else None
        logger.warning(
            "whatsapp_call_action_failed action=%s call_id=%s status=%s body=%s",
            action,
            call_id,
            status_code,
            body,
        )
        raise InboxExternalError(
            "whatsapp_call_action_failed",
            detail=f"WhatsApp call action '{action}' failed",
            status_code=status_code or 502,
            retryable=(status_code is not None and status_code >= 500),
        ) from exc

    logger.info(
        "whatsapp_call_action_ok action=%s call_id=%s status=%s response=%s",
        action,
        call_id,
        response_status,
        response_json,
    )
    if action == "accept":
        _stamp_call_accept_metadata(db, call_id, actor_person_id)
        db.commit()
    return {
        "call_id": call_id,
        "action": action,
        "phone_number_id": phone_number_id,
        "status_code": response_status,
        "provider_response": response_json,
    }


def _coerce_ice_server_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    urls = value.get("urls")
    if isinstance(urls, str):
        urls = [urls]
    if isinstance(urls, list):
        normalized_urls = [str(item).strip() for item in urls if isinstance(item, str) and item.strip()]
        if not normalized_urls:
            return None
        value = dict(value)
        value["urls"] = normalized_urls
        return value
    return None


def get_whatsapp_webrtc_config(db: Session) -> dict[str, Any]:
    default_ice = [{"urls": ["stun:stun.l.google.com:19302"]}]

    raw_ice = resolve_value(db, SettingDomain.comms, "whatsapp_stun_servers")
    if not isinstance(raw_ice, list):
        raw_ice = default_ice
    ice_servers = []
    for entry in raw_ice:
        normalized = _coerce_ice_server_dict(entry)
        if normalized is not None:
            ice_servers.append(normalized)

    raw_turn = resolve_value(db, SettingDomain.comms, "whatsapp_turn_servers")
    if isinstance(raw_turn, list):
        for entry in raw_turn:
            normalized = _coerce_ice_server_dict(entry)
            if normalized is not None:
                ice_servers.append(normalized)

    return {"ice_servers": ice_servers}
