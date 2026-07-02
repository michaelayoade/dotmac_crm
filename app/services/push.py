"""Mobile push notifications via FCM HTTP v1.

FCM covers both Android and iOS (via APNs relay), so this is the single push
integration for the field app.

Configuration (environment variables, no hardcoded secrets):
- ``FCM_SERVICE_ACCOUNT_JSON``: service-account JSON content, or a path to it
- ``FCM_PROJECT_ID``: optional override; defaults to the service account's project

When unconfigured, sends are skipped and recorded as failed deliveries so the
rest of the pipeline (device registry, notification audit rows) keeps working.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import HTTPException
from jose import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit import AuditActorType, AuditEvent
from app.models.field import DevicePlatform, DeviceToken
from app.models.notification import (
    DeliveryStatus,
    Notification,
    NotificationChannel,
    NotificationDelivery,
    NotificationStatus,
)
from app.services.common import coerce_uuid, validate_enum
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)

_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_FCM_SEND_TIMEOUT_SECONDS = 10.0
_DEDUPE_WINDOW_SECONDS = 600
# Cache the OAuth access token until shortly before expiry.
_cached_access_token: str | None = None
_cached_token_expires_at: float = 0.0


def _load_service_account() -> dict | None:
    raw = os.getenv("FCM_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    try:
        if raw.startswith("{"):
            return json.loads(raw)
        path = Path(raw)
        if path.is_file():
            return json.loads(path.read_text())
    except (OSError, ValueError):
        logger.warning("push_service_account_unreadable")
    return None


def is_configured() -> bool:
    return _load_service_account() is not None


def _project_id(account: dict) -> str | None:
    return os.getenv("FCM_PROJECT_ID") or account.get("project_id")


def _get_access_token(account: dict) -> str:
    """Mint (or reuse) an OAuth2 access token via the JWT-bearer grant."""
    global _cached_access_token, _cached_token_expires_at
    now = time.time()
    if _cached_access_token and now < _cached_token_expires_at - 60:
        return _cached_access_token

    token_uri = account.get("token_uri", "https://oauth2.googleapis.com/token")
    issued_at = int(now)
    assertion = jwt.encode(
        {
            "iss": account["client_email"],
            "scope": _FCM_SCOPE,
            "aud": token_uri,
            "iat": issued_at,
            "exp": issued_at + 3600,
        },
        account["private_key"],
        algorithm="RS256",
    )
    response = httpx.post(
        token_uri,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
        timeout=_FCM_SEND_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    _cached_access_token = str(payload["access_token"])
    _cached_token_expires_at = now + int(payload.get("expires_in", 3600))
    return _cached_access_token


class _TokenInvalid(Exception):
    """The FCM registration token is no longer valid and must be pruned."""


def _send_fcm_message(account: dict, fcm_token: str, title: str, body: str, data: dict | None) -> str:
    project_id = _project_id(account)
    if not project_id:
        raise RuntimeError("FCM project id missing")
    access_token = _get_access_token(account)
    message: dict = {
        "message": {
            "token": fcm_token,
            "notification": {"title": title[:200], "body": body[:1000]},
        }
    }
    if data:
        message["message"]["data"] = {str(k): str(v) for k, v in data.items()}
    response = httpx.post(
        f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send",
        json=message,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=_FCM_SEND_TIMEOUT_SECONDS,
    )
    if response.status_code in (400, 404, 410):
        detail = response.text
        if "UNREGISTERED" in detail or ("INVALID_ARGUMENT" in detail and "token" in detail.lower()):
            raise _TokenInvalid(detail[:200])
    response.raise_for_status()
    return str(response.json().get("name", ""))


class PushDevices(ListResponseMixin):
    @staticmethod
    def register(
        db: Session,
        *,
        platform: str,
        fcm_token: str,
        person_id: str | None = None,
        vendor_user_id: str | None = None,
        app_version: str | None = None,
    ) -> DeviceToken:
        if bool(person_id) == bool(vendor_user_id):
            raise HTTPException(status_code=422, detail="Exactly one of person_id or vendor_user_id is required")
        if not fcm_token or not fcm_token.strip():
            raise HTTPException(status_code=422, detail="fcm_token is required")
        platform_value = validate_enum(platform, DevicePlatform, "platform")

        fcm_token = fcm_token.strip()
        now = datetime.now(UTC)
        # Token rotation / device handover: re-registering an existing token
        # re-points it at the current owner.
        device = db.query(DeviceToken).filter(DeviceToken.fcm_token == fcm_token).first()
        if device:
            device.person_id = coerce_uuid(person_id) if person_id else None
            device.vendor_user_id = coerce_uuid(vendor_user_id) if vendor_user_id else None
            device.platform = platform_value
            device.app_version = app_version
            device.last_seen_at = now
            device.is_active = True
            db.commit()
            db.refresh(device)
            return device

        device = DeviceToken(
            person_id=coerce_uuid(person_id) if person_id else None,
            vendor_user_id=coerce_uuid(vendor_user_id) if vendor_user_id else None,
            platform=platform_value,
            fcm_token=fcm_token,
            app_version=app_version,
            last_seen_at=now,
        )
        db.add(device)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.query(DeviceToken).filter(DeviceToken.fcm_token == fcm_token).first()
            if existing:
                return existing
            raise
        db.refresh(device)
        return device

    @staticmethod
    def active_tokens_for_person(db: Session, person_id: str) -> list[DeviceToken]:
        return (
            db.query(DeviceToken)
            .filter(DeviceToken.person_id == coerce_uuid(person_id))
            .filter(DeviceToken.is_active.is_(True))
            .all()
        )

    @staticmethod
    def active_tokens_for_vendor_user(db: Session, vendor_user_id: str) -> list[DeviceToken]:
        return (
            db.query(DeviceToken)
            .filter(DeviceToken.vendor_user_id == coerce_uuid(vendor_user_id))
            .filter(DeviceToken.is_active.is_(True))
            .all()
        )

    @staticmethod
    def list_for_person(db: Session, person_id: str) -> list[DeviceToken]:
        """The caller's own active devices, most-recently-seen first."""
        return (
            db.query(DeviceToken)
            .filter(DeviceToken.person_id == coerce_uuid(person_id))
            .filter(DeviceToken.is_active.is_(True))
            .order_by(DeviceToken.last_seen_at.desc().nullslast(), DeviceToken.created_at.desc())
            .all()
        )

    @staticmethod
    def deregister(db: Session, *, device_id: str, person_id: str) -> None:
        """Soft-deactivate one of the caller's own devices (logout / lost phone).

        Scoped to the caller: a device owned by someone else yields a uniform
        404 so an id probe can't enumerate or remove others' devices. An audit
        row records the removal.
        """
        device = (
            db.query(DeviceToken)
            .filter(DeviceToken.id == coerce_uuid(device_id))
            .filter(DeviceToken.person_id == coerce_uuid(person_id))
            .filter(DeviceToken.is_active.is_(True))
            .first()
        )
        if device is None:
            raise HTTPException(status_code=404, detail="Device not found")
        device.is_active = False
        db.add(
            AuditEvent(
                actor_type=AuditActorType.user,
                actor_id=str(person_id),
                action="field:device:deregister",
                entity_type="DeviceToken",
                entity_id=str(device.id),
                status_code=200,
                is_success=True,
                metadata_={"platform": device.platform.value},
            )
        )
        db.commit()
        logger.info("push_device_deregistered device_id=%s person_id=%s", device.id, person_id)


class PushSender:
    @staticmethod
    def _is_duplicate(db: Session, recipient: str, subject: str, body: str) -> bool:
        """House rule: suppress a genuine resend within the window.

        Keys on (recipient, subject, body) — the body carries the per-job
        discriminator (e.g. the work order title), so two DIFFERENT job
        assignments to the same tech are not mistaken for duplicates while a
        true resend of the same notification still is.
        """
        cutoff = datetime.now(UTC).timestamp() - _DEDUPE_WINDOW_SECONDS
        recent = (
            db.query(Notification)
            .filter(Notification.channel == NotificationChannel.push)
            .filter(Notification.recipient == recipient)
            .filter(Notification.subject == subject)
            .filter(Notification.body == body)
            .order_by(Notification.created_at.desc())
            .first()
        )
        if not recent or not recent.created_at:
            return False
        created = recent.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return created.timestamp() > cutoff

    @staticmethod
    def send_to_person(
        db: Session,
        person_id: str,
        *,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> dict:
        tokens = PushDevices.active_tokens_for_person(db, person_id)
        return PushSender._send_to_tokens(db, tokens, recipient=str(person_id), title=title, body=body, data=data)

    @staticmethod
    def send_to_vendor_user(
        db: Session,
        vendor_user_id: str,
        *,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> dict:
        tokens = PushDevices.active_tokens_for_vendor_user(db, vendor_user_id)
        return PushSender._send_to_tokens(db, tokens, recipient=str(vendor_user_id), title=title, body=body, data=data)

    @staticmethod
    def _send_to_tokens(
        db: Session,
        tokens: list[DeviceToken],
        *,
        recipient: str,
        title: str,
        body: str,
        data: dict | None,
    ) -> dict:
        results = {"sent": 0, "failed": 0, "pruned": 0, "skipped": 0}
        if not tokens:
            results["skipped"] += 1
            return results
        if PushSender._is_duplicate(db, recipient, title, body):
            logger.info("push_skipped_duplicate recipient=%s subject=%s", recipient, title)
            results["skipped"] += 1
            return results

        account = _load_service_account()
        now = datetime.now(UTC)
        notification = Notification(
            channel=NotificationChannel.push,
            recipient=recipient,
            subject=title[:200],
            body=body,
            status=NotificationStatus.sending,
        )
        db.add(notification)
        db.commit()
        db.refresh(notification)

        any_sent = False
        for device in tokens:
            delivery = NotificationDelivery(notification_id=notification.id, provider="fcm")
            if account is None:
                delivery.status = DeliveryStatus.failed
                delivery.response_body = "FCM not configured"
                results["failed"] += 1
            else:
                try:
                    message_id = _send_fcm_message(account, device.fcm_token, title, body, data)
                    delivery.status = DeliveryStatus.delivered
                    delivery.provider_message_id = message_id[:200]
                    device.last_seen_at = now
                    any_sent = True
                    results["sent"] += 1
                except _TokenInvalid as exc:
                    device.is_active = False
                    delivery.status = DeliveryStatus.rejected
                    delivery.response_body = f"token pruned: {exc}"[:500]
                    results["pruned"] += 1
                except Exception as exc:  # one bad device must not stop the rest
                    delivery.status = DeliveryStatus.failed
                    delivery.response_body = str(exc)[:500]
                    results["failed"] += 1
                    logger.warning("push_send_failed device_id=%s error=%s", device.id, exc)
            db.add(delivery)

        notification.status = NotificationStatus.delivered if any_sent else NotificationStatus.failed
        notification.sent_at = now if any_sent else None
        db.commit()
        return results


push_devices = PushDevices()
push_sender = PushSender()


def queue_work_order_assignment_push(db: Session, work_order) -> None:
    """Enqueue an assignment push for the work order's assigned technician.

    Falls back to a synchronous send when the Celery broker is unavailable so
    tests and degraded environments still deliver.
    """
    if not work_order.assigned_to_person_id:
        return
    person_id = str(work_order.assigned_to_person_id)
    title = "New job assigned"
    body = f"{work_order.title} — open the app for details"
    data = {
        "type": "work_order_assigned",
        "work_order_id": str(work_order.id),
    }
    try:
        from app.tasks.push import send_push_to_person

        send_push_to_person.delay(person_id=person_id, title=title, body=body, data=data)
    except Exception:
        logger.debug("push_enqueue_failed_falling_back_to_sync", exc_info=True)
        try:
            push_sender.send_to_person(db, person_id, title=title, body=body, data=data)
        except Exception:
            logger.warning("push_sync_fallback_failed work_order_id=%s", work_order.id, exc_info=True)


def queue_vendor_quote_approved_push(db: Session, quote) -> None:
    """Notify a vendor's crew that their bid was approved.

    Pushes to every active VendorUser of the quote's vendor (a vendor may have
    several logins). Falls back to a synchronous send when the Celery broker is
    unavailable so tests and degraded environments still deliver.
    """
    from app.models.vendor import VendorUser

    project = quote.project
    project_name = project.project.name if project and project.project else "your project"
    title = "Bid approved"
    body = f"Your bid for {project_name} was approved — you can start work."
    data = {
        "type": "vendor_quote_approved",
        "quote_id": str(quote.id),
        "installation_project_id": str(quote.project_id),
    }
    vendor_user_ids = [
        str(vu.id)
        for vu in db.query(VendorUser)
        .filter(VendorUser.vendor_id == quote.vendor_id, VendorUser.is_active.is_(True))
        .all()
    ]
    for vendor_user_id in vendor_user_ids:
        try:
            from app.tasks.push import send_push_to_vendor_user

            send_push_to_vendor_user.delay(vendor_user_id=vendor_user_id, title=title, body=body, data=data)
        except Exception:
            logger.debug("vendor_push_enqueue_failed_falling_back_to_sync", exc_info=True)
            try:
                push_sender.send_to_vendor_user(db, vendor_user_id, title=title, body=body, data=data)
            except Exception:
                logger.warning("vendor_push_sync_fallback_failed quote_id=%s", quote.id, exc_info=True)
