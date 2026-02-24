"""Service helpers for admin notifications dropdown."""

import re

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.services import notification as notification_service
from app.services import web_admin as web_admin_service

templates = Jinja2Templates(directory="templates")


def _extract_target_url(body: str | None) -> str | None:
    if not isinstance(body, str) or not body.strip():
        return None

    # Prefer explicit "Open: <url>" pattern used by assignment notifications.
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.lower().startswith("open:"):
            continue
        candidate = line.split(":", 1)[1].strip()
        if candidate.startswith(("http://", "https://", "/")):
            return candidate

    # Fallback: first URL-like token anywhere in body.
    match = re.search(r"(https?://\S+|/\S+)", body)
    if not match:
        return None
    return match.group(1).strip()


def _is_ticket_assignment_notification(notification: Notification) -> bool:
    subject = getattr(notification, "subject", None)
    if not isinstance(subject, str):
        return False
    return subject.strip().lower().startswith("new ticket assignment:")


def notifications_menu(request: Request, db: Session):
    current_user = web_admin_service.get_current_user(request)
    recipients = {
        current_user.get("email"),
        current_user.get("person_id"),
        current_user.get("id"),
    }
    recipients.discard(None)
    recipients.discard("")

    if recipients:
        notifications = (
            db.query(Notification)
            .filter(Notification.is_active.is_(True))
            .filter(Notification.recipient.in_(list(recipients)))
            .order_by(Notification.created_at.desc())
            .limit(10)
            .all()
        )
    else:
        notifications = notification_service.notifications.list(
            db=db,
            channel=None,
            status=None,
            is_active=True,
            order_by="created_at",
            order_dir="desc",
            limit=10,
            offset=0,
        )

    notification_items = [
        {
            "notification": notification,
            "target_url": (
                _extract_target_url(getattr(notification, "body", None))
                if _is_ticket_assignment_notification(notification)
                else None
            ),
        }
        for notification in notifications
    ]

    return templates.TemplateResponse(
        "admin/partials/notifications_menu.html",
        {"request": request, "notifications": notifications, "notification_items": notification_items},
    )
