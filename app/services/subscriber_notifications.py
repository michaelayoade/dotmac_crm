from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from html import escape
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingValueType
from app.models.event_store import EventStore
from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.person import ChannelType, Person
from app.models.subscriber import Subscriber
from app.models.subscriber_notification import SubscriberNotificationLog
from app.models.tickets import Ticket, TicketComment, TicketPriority, TicketStatus
from app.schemas.settings import DomainSettingUpdate
from app.services.branding import get_branding
from app.services.domain_settings import notification_settings

DEDUPLICATE_WINDOW = timedelta(hours=6)
SEND_WINDOW_START_HOUR = 9
SEND_WINDOW_END_HOUR = 18
RECENT_ACTIVITY_WINDOW = timedelta(days=7)
TEMPLATE_SETTING_KEY = "subscriber_online_last_24h_templates"

_OPEN_TICKET_STATUSES = {
    TicketStatus.new,
    TicketStatus.open,
    TicketStatus.pending,
    TicketStatus.waiting_on_customer,
}
_RESOLVED_TICKET_STATUSES = {
    TicketStatus.closed,
    TicketStatus.canceled,
    TicketStatus.merged,
}
_ESCALATED_TICKET_STATUSES = {
    TicketStatus.on_hold,
    TicketStatus.lastmile_rerun,
    TicketStatus.site_under_construction,
}
_ESCALATED_TICKET_PRIORITIES = {
    TicketPriority.high,
    TicketPriority.urgent,
}

TEMPLATE_TOKENS = ("{name}", "{last_seen}", "{support_email}", "{last_activity}")

EMAIL_SUBJECTS = {
    "friendly_check_in": "Checking in on your connection",
    "issue_reference": "Update on your support issue",
    "escalated_formal": "Escalated support follow-up",
    "resolved_invite_back": "Confirming your issue was resolved",
}

EMAIL_TEMPLATES = {
    "friendly_check_in": (
        "Hi {name}, we noticed activity from your account around {last_seen}. "
        "Everything looks online from our side, but we wanted to check in and make sure your service is working as expected. "
        "If you need anything, reply to {support_email} and our team will help. "
        "Thanks for staying with us."
    ),
    "issue_reference": (
        "Hi {name}, we can see your service was active again around {last_seen}. "
        "Your current support issue is still open, and the latest activity we recorded is {last_activity}. "
        "We are continuing to monitor the case and will keep you updated if anything changes. "
        "If you need to add details, contact us at {support_email}."
    ),
    "escalated_formal": (
        "Hello {name}, your case remains under escalated review following activity recorded at {last_seen}. "
        "Our team is treating the latest activity, {last_activity}, as part of the active investigation. "
        "If you would like a callback, reply to {support_email} with your preferred contact time. "
        "We will continue to share formal updates until the matter is closed."
    ),
    "resolved_invite_back": (
        "Hi {name}, we are following up after seeing activity on your account at {last_seen}. "
        "Your earlier issue appears resolved, and the latest activity logged was {last_activity}. "
        "If everything is working well, no action is needed from you. "
        "If anything feels off, contact {support_email} and we will reopen support quickly."
    ),
}

SMS_TEMPLATES = {
    "friendly_check_in": "Hi {name}, we saw activity at {last_seen}. If you need help, email {support_email}.",
    "issue_reference": "Hi {name}, your issue is still open. Last activity: {last_activity}. Reply via {support_email} if needed.",
    "escalated_formal": "Hello {name}, your case is escalated. Last activity: {last_activity}. Email {support_email} to request a callback.",
    "resolved_invite_back": "Hi {name}, your issue appears resolved. We saw activity at {last_seen}. Need help again? {support_email}",
}

TEMPLATE_LABELS = {
    "friendly_check_in": "No Ticket",
    "issue_reference": "Open / Pending",
    "escalated_formal": "Escalated",
    "resolved_invite_back": "Closed / Resolved",
}


@dataclass
class NotificationTemplateBundle:
    template_key: str
    email_subject: str
    email_body: str
    sms_body: str


@dataclass
class PreparedSubscriberNotification:
    subscriber: Subscriber
    person: Person | None
    ticket: Ticket | None
    timezone_name: str
    template: NotificationTemplateBundle
    token_values: dict[str, str]


@dataclass
class SubscriberPriorityScore:
    value: int
    label: str
    reasons: list[str]


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _display_name(person: Person | None, subscriber: Subscriber) -> str:
    if person is None:
        return subscriber.subscriber_number or "Customer"
    return (
        person.display_name
        or f"{person.first_name or ''} {person.last_name or ''}".strip()
        or person.email
        or subscriber.subscriber_number
        or "Customer"
    )


def _format_last_seen(value: datetime | None, timezone_name: str) -> str:
    if value is None:
        return "recently"
    tz = _resolve_timezone(timezone_name)
    return value.astimezone(tz).strftime("%b %d, %Y %I:%M %p")


def _resolve_timezone(timezone_name: str | None) -> ZoneInfo:
    candidate = (timezone_name or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _latest_ticket_for_subscriber(db: Session, subscriber_id: UUID) -> Ticket | None:
    return db.scalar(
        select(Ticket)
        .where(
            Ticket.is_active.is_(True),
            Ticket.subscriber_id == subscriber_id,
        )
        .order_by(Ticket.created_at.desc())
        .limit(1)
    )


def _default_template_map() -> dict[str, dict[str, str]]:
    return {
        key: {
            "label": TEMPLATE_LABELS[key],
            "email_subject": EMAIL_SUBJECTS[key],
            "email_body": EMAIL_TEMPLATES[key],
            "sms_body": SMS_TEMPLATES[key],
        }
        for key in EMAIL_SUBJECTS
    }


def _load_template_map(db: Session) -> dict[str, dict[str, str]]:
    templates = _default_template_map()
    try:
        setting = notification_settings.get_by_key(db, TEMPLATE_SETTING_KEY)
    except HTTPException:
        setting = None
    payload = setting.value_json if setting is not None else None
    if not isinstance(payload, dict):
        return templates
    for template_key, template_values in payload.items():
        if template_key not in templates or not isinstance(template_values, dict):
            continue
        merged = dict(templates[template_key])
        for field in ("email_subject", "email_body", "sms_body"):
            value = str(template_values.get(field) or "").strip()
            if value:
                merged[field] = value
        templates[template_key] = merged
    return templates


def save_template_bundle(
    db: Session,
    *,
    template_key: str,
    email_subject: str,
    email_body: str,
    sms_body: str,
) -> dict[str, str]:
    normalized_key = (template_key or "").strip()
    if normalized_key not in EMAIL_SUBJECTS:
        raise HTTPException(status_code=400, detail="Unknown template status.")
    templates = _load_template_map(db)
    templates[normalized_key] = {
        "label": TEMPLATE_LABELS[normalized_key],
        "email_subject": email_subject.strip(),
        "email_body": email_body.strip(),
        "sms_body": sms_body.strip(),
    }
    notification_settings.upsert_by_key(
        db,
        TEMPLATE_SETTING_KEY,
        DomainSettingUpdate(
            value_type=SettingValueType.json,
            value_json=templates,
            value_text=None,
            is_active=True,
        ),
    )
    return templates[normalized_key]


def _select_template_key(ticket: Ticket | None) -> str:
    if ticket is None:
        return "friendly_check_in"
    if ticket.status in _RESOLVED_TICKET_STATUSES:
        return "resolved_invite_back"
    if ticket.status in _ESCALATED_TICKET_STATUSES or ticket.priority in _ESCALATED_TICKET_PRIORITIES:
        return "escalated_formal"
    if ticket.status in _OPEN_TICKET_STATUSES:
        return "issue_reference"
    return "issue_reference"


def _render_template(template: str, token_values: dict[str, str]) -> str:
    rendered = template
    for token, value in token_values.items():
        rendered = rendered.replace(token, value)
    return " ".join(rendered.split())


def _count_sentences(text: str) -> int:
    matches = re.findall(r"[.!?](?:\s|$)", text.replace("\n", " "))
    return len(matches) if matches else (1 if text.strip() else 0)


def _next_allowed_send_time(now_local: datetime) -> datetime:
    start_of_window = now_local.replace(
        hour=SEND_WINDOW_START_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )
    end_of_window = now_local.replace(
        hour=SEND_WINDOW_END_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )
    if now_local < start_of_window:
        return start_of_window
    if now_local >= end_of_window:
        return (start_of_window + timedelta(days=1)).replace(
            hour=SEND_WINDOW_START_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
    return now_local


def _validate_scheduled_local(dt_local: datetime) -> None:
    if dt_local.time() < time(hour=SEND_WINDOW_START_HOUR) or dt_local.time() >= time(hour=SEND_WINDOW_END_HOUR):
        raise HTTPException(
            status_code=400,
            detail="Scheduled notifications must be between 9:00 AM and 6:00 PM in the customer timezone.",
        )


def _preferred_sms_number(person: Person | None) -> str | None:
    if person is None:
        return None
    channels = getattr(person, "channels", None) or []
    for preferred_type in (ChannelType.sms, ChannelType.phone, ChannelType.whatsapp):
        for channel in channels:
            if channel.channel_type == preferred_type and channel.address:
                return str(channel.address).strip()
    if person.phone:
        return str(person.phone).strip()
    return None


def prepare_subscriber_notification(db: Session, subscriber_id: UUID) -> PreparedSubscriberNotification:
    subscriber = db.get(Subscriber, subscriber_id)
    if subscriber is None or not subscriber.is_active:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    person = db.get(Person, subscriber.person_id) if subscriber.person_id else None
    ticket = _latest_ticket_for_subscriber(db, subscriber.id)
    branding = get_branding(db)
    timezone_name = (person.timezone if person and person.timezone else "UTC").strip() or "UTC"
    support_email = branding.get("support_email") or "support@example.com"
    token_values = {
        "{name}": _display_name(person, subscriber),
        "{last_seen}": "recently",
        "{support_email}": str(support_email),
        "{last_activity}": "recent account activity",
    }
    template_key = _select_template_key(ticket)
    template_map = _load_template_map(db)
    template_values = template_map[template_key]
    template = NotificationTemplateBundle(
        template_key=template_key,
        email_subject=template_values["email_subject"],
        email_body=template_values["email_body"],
        sms_body=template_values["sms_body"],
    )
    return PreparedSubscriberNotification(
        subscriber=subscriber,
        person=person,
        ticket=ticket,
        timezone_name=timezone_name,
        template=template,
        token_values=token_values,
    )


def _build_activity_log(
    db: Session, subscriber_id: UUID, ticket: Ticket | None, timezone_name: str
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    tz = _resolve_timezone(timezone_name)

    logs = db.scalars(
        select(SubscriberNotificationLog)
        .where(SubscriberNotificationLog.subscriber_id == subscriber_id)
        .order_by(SubscriberNotificationLog.created_at.desc())
        .limit(8)
    ).all()
    for log in logs:
        created_at = _coerce_utc(log.created_at)
        entries.append(
            {
                "kind": "notification",
                "timestamp": created_at.astimezone(tz).strftime("%b %d, %Y %I:%M %p") if created_at else "",
                "title": f"{log.channel.value.upper()} notification queued",
                "detail": log.message_body,
            }
        )

    if ticket is not None:
        ticket_stamp = _coerce_utc(ticket.updated_at) or _coerce_utc(ticket.created_at)
        entries.append(
            {
                "kind": "ticket_status",
                "timestamp": ticket_stamp.astimezone(tz).strftime("%b %d, %Y %I:%M %p") if ticket_stamp else "",
                "title": f"Ticket {ticket.status.value.replace('_', ' ').title()}",
                "detail": ticket.title,
            }
        )
        comments = db.scalars(
            select(TicketComment)
            .where(TicketComment.ticket_id == ticket.id)
            .order_by(TicketComment.created_at.desc())
            .limit(6)
        ).all()
        for comment in comments:
            comment_stamp = _coerce_utc(comment.created_at)
            entries.append(
                {
                    "kind": "ticket_comment",
                    "timestamp": comment_stamp.astimezone(tz).strftime("%b %d, %Y %I:%M %p") if comment_stamp else "",
                    "title": "Ticket comment",
                    "detail": comment.body,
                }
            )

    entries.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    return entries[:10]


def _priority_score(
    db: Session,
    *,
    subscriber_id: UUID,
    ticket: Ticket | None,
    last_seen_at: datetime | None,
) -> SubscriberPriorityScore:
    now = datetime.now(UTC)
    score = 0
    reasons: list[str] = []

    if last_seen_at is not None:
        hours_since_last_seen = max(0.0, (now - last_seen_at).total_seconds() / 3600.0)
        stale_points = min(35, int(hours_since_last_seen * 2))
        score += stale_points
        if stale_points:
            reasons.append(f"Last seen {hours_since_last_seen:.1f}h ago")

    recent_activity_count = int(
        db.scalar(
            select(func.count(EventStore.id)).where(
                EventStore.is_active.is_(True),
                EventStore.subscriber_id == subscriber_id,
                EventStore.created_at >= now - RECENT_ACTIVITY_WINDOW,
                EventStore.event_type.in_(["session.started", "device.online", "usage.recorded"]),
            )
        )
        or 0
    )
    if recent_activity_count >= 20:
        score += 18
        reasons.append("Usually active recently")
    elif recent_activity_count >= 8:
        score += 10
        reasons.append("Moderately active recently")

    if ticket is not None:
        if ticket.status in _ESCALATED_TICKET_STATUSES or ticket.priority in _ESCALATED_TICKET_PRIORITIES:
            score += 25
            reasons.append("Escalated ticket")
        elif ticket.status in _OPEN_TICKET_STATUSES:
            score += 15
            reasons.append("Open ticket")
        elif ticket.status in _RESOLVED_TICKET_STATUSES:
            score += 5
            reasons.append("Recently resolved ticket")

    recent_notification_count = int(
        db.scalar(
            select(func.count(SubscriberNotificationLog.id)).where(
                SubscriberNotificationLog.subscriber_id == subscriber_id,
                SubscriberNotificationLog.created_at >= now - RECENT_ACTIVITY_WINDOW,
            )
        )
        or 0
    )
    if recent_notification_count:
        score += min(20, recent_notification_count * 5)
        reasons.append("Recent notifications already queued")

    score = min(100, score)
    if score >= 70:
        label = "High"
    elif score >= 40:
        label = "Medium"
    else:
        label = "Low"
    return SubscriberPriorityScore(value=score, label=label, reasons=reasons[:3])


def notification_context_for_subscriber(
    db: Session,
    *,
    subscriber_id: UUID,
    last_seen_text: str | None = None,
    last_activity: str | None = None,
) -> dict[str, Any]:
    prepared = prepare_subscriber_notification(db, subscriber_id)
    templates = _load_template_map(db)
    tokens = dict(prepared.token_values)
    tokens["{last_seen}"] = (last_seen_text or "").strip() or "recently"
    tokens["{last_activity}"] = (last_activity or "").strip() or "recent account activity"

    last_seen_at = None
    if isinstance(last_seen_text, str) and last_seen_text.strip():
        for fmt in ("%b %d, %Y %I:%M %p", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
            try:
                last_seen_at = datetime.strptime(last_seen_text.strip(), fmt).replace(tzinfo=UTC)
                break
            except ValueError:
                continue

    rendered_templates: dict[str, dict[str, str]] = {}
    for template_key, template_values in templates.items():
        rendered_templates[template_key] = {
            "label": template_values["label"],
            "email_subject": template_values["email_subject"],
            "email_body": _render_template(template_values["email_body"], tokens),
            "sms_body": _render_template(template_values["sms_body"], tokens),
        }

    priority = _priority_score(
        db,
        subscriber_id=prepared.subscriber.id,
        ticket=prepared.ticket,
        last_seen_at=last_seen_at,
    )
    return {
        "template_key": prepared.template.template_key,
        "templates": rendered_templates,
        "token_list": list(TEMPLATE_TOKENS),
        "priority": {
            "value": priority.value,
            "label": priority.label,
            "reasons": priority.reasons,
        },
        "activity_log": _build_activity_log(db, prepared.subscriber.id, prepared.ticket, prepared.timezone_name),
    }


def enrich_notification_rows(rows: list[dict[str, Any]], db: Session) -> list[dict[str, Any]]:
    subscriber_ids: list[UUID] = []
    for row in rows:
        try:
            subscriber_ids.append(UUID(str(row["subscriber_id"])))
        except Exception:
            continue

    latest_logs: dict[str, SubscriberNotificationLog] = {}
    if subscriber_ids:
        ranked_logs = (
            select(
                SubscriberNotificationLog,
                Notification.status.label("notification_status"),
            )
            .join(
                Notification,
                Notification.id == SubscriberNotificationLog.notification_id,
                isouter=True,
            )
            .where(SubscriberNotificationLog.subscriber_id.in_(subscriber_ids))
            .order_by(SubscriberNotificationLog.subscriber_id.asc(), SubscriberNotificationLog.created_at.desc())
        )
        for log, notification_status in db.execute(ranked_logs).all():
            subscriber_key = str(log.subscriber_id)
            if subscriber_key in latest_logs:
                continue
            log._notification_status_value = notification_status.value if notification_status else "testing_hold"
            latest_logs[subscriber_key] = log

    for row in rows:
        try:
            prepared = prepare_subscriber_notification(db, UUID(str(row["subscriber_id"])))
        except Exception:
            continue
        last_seen_iso = row.get("last_seen_at_iso")
        last_seen_value = None
        if isinstance(last_seen_iso, str) and last_seen_iso:
            try:
                last_seen_value = datetime.fromisoformat(last_seen_iso)
            except ValueError:
                last_seen_value = None
        last_activity = str(row.get("last_activity") or row.get("last_seen_at") or "recent account activity")
        prepared.token_values["{last_seen}"] = _format_last_seen(last_seen_value, prepared.timezone_name)
        prepared.token_values["{last_activity}"] = last_activity
        row["notification_timezone"] = prepared.timezone_name
        row["notification_template_key"] = prepared.template.template_key
        row["notification_email_subject"] = prepared.template.email_subject
        row["notification_email_body"] = _render_template(prepared.template.email_body, prepared.token_values)
        row["notification_sms_body"] = _render_template(prepared.template.sms_body, prepared.token_values)
        row["notification_tokens"] = ", ".join(TEMPLATE_TOKENS)
        latest_log = latest_logs.get(str(prepared.subscriber.id))
        if latest_log is not None:
            scheduled_local = _format_last_seen(latest_log.scheduled_for_at, prepared.timezone_name)
            row["latest_notification_channel"] = latest_log.channel.value
            row["latest_notification_status"] = getattr(latest_log, "_notification_status_value", "queued")
            row["latest_notification_scheduled_for"] = scheduled_local
            row["latest_notification_message_body"] = latest_log.message_body
    return rows


def _effective_send_at(
    timezone_name: str,
    scheduled_local_text: str | None,
    now_utc: datetime | None = None,
) -> tuple[datetime, str]:
    tz = _resolve_timezone(timezone_name)
    now_utc = _coerce_utc(now_utc) or datetime.now(UTC)
    now_local = now_utc.astimezone(tz)
    if scheduled_local_text:
        try:
            scheduled_local = datetime.fromisoformat(scheduled_local_text.strip())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid schedule date/time.") from exc
        if scheduled_local.tzinfo is not None:
            scheduled_local = scheduled_local.astimezone(tz).replace(tzinfo=None)
        scheduled_local = scheduled_local.replace(tzinfo=tz)
        _validate_scheduled_local(scheduled_local)
        if scheduled_local.astimezone(UTC) < now_utc:
            raise HTTPException(status_code=400, detail="Scheduled time must be in the future.")
        return scheduled_local.astimezone(UTC), scheduled_local.strftime("%Y-%m-%dT%H:%M")

    send_local = _next_allowed_send_time(now_local)
    return send_local.astimezone(UTC), send_local.strftime("%Y-%m-%dT%H:%M")


def _recent_notification_exists(db: Session, subscriber_id: UUID, now_utc: datetime | None = None) -> bool:
    now_utc = _coerce_utc(now_utc) or datetime.now(UTC)
    cutoff = now_utc - DEDUPLICATE_WINDOW
    existing = db.scalar(
        select(SubscriberNotificationLog.id)
        .where(
            SubscriberNotificationLog.subscriber_id == subscriber_id,
            SubscriberNotificationLog.created_at >= cutoff,
        )
        .limit(1)
    )
    return existing is not None


def _create_notification(
    *,
    db: Session,
    channel: NotificationChannel,
    recipient: str,
    send_at: datetime,
    subject: str | None,
    body: str,
) -> Notification:
    notification = Notification(
        channel=channel,
        recipient=recipient,
        subject=subject,
        body=body if channel == NotificationChannel.sms else escape(body).replace("\n", "<br>"),
        status=NotificationStatus.queued,
        send_at=send_at,
    )
    db.add(notification)
    db.flush()
    return notification


def queue_subscriber_notification(
    db: Session,
    *,
    subscriber_id: UUID,
    channel_value: str,
    email_subject: str | None,
    email_body: str | None,
    sms_body: str | None,
    scheduled_local_text: str | None,
    sent_by_user_id: UUID | None,
    sent_by_person_id: UUID | None,
) -> list[SubscriberNotificationLog]:
    prepared = prepare_subscriber_notification(db, subscriber_id)
    if _recent_notification_exists(db, prepared.subscriber.id):
        raise HTTPException(
            status_code=409, detail="A notification was already sent to this customer in the last 6 hours."
        )
    if sent_by_person_id and db.get(Person, sent_by_person_id) is None:
        sent_by_person_id = None

    channel_normalized = (channel_value or "").strip().lower()
    if channel_normalized not in {"email", "sms", "both"}:
        raise HTTPException(status_code=400, detail="Invalid notification channel.")

    send_at_utc, _display_local = _effective_send_at(prepared.timezone_name, scheduled_local_text)
    logs: list[SubscriberNotificationLog] = []
    email_address = prepared.person.email.strip() if prepared.person and prepared.person.email else None
    sms_number = _preferred_sms_number(prepared.person)
    email_subject_value = (email_subject or "").strip()
    email_body_value = (email_body or "").strip()
    sms_body_value = (sms_body or "").strip()

    if channel_normalized in {"email", "both"}:
        if not email_address:
            raise HTTPException(status_code=400, detail="Customer does not have an email address.")
        if not email_subject_value:
            raise HTTPException(status_code=400, detail="Email subject is required.")
        if not email_body_value:
            raise HTTPException(status_code=400, detail="Email body is required.")
        if _count_sentences(email_body_value) > 4:
            raise HTTPException(status_code=400, detail="Emails must stay within 3 to 4 short sentences.")

    if channel_normalized in {"sms", "both"}:
        if not sms_number:
            raise HTTPException(status_code=400, detail="Customer does not have a mobile number for SMS.")
        if not sms_body_value:
            raise HTTPException(status_code=400, detail="SMS body is required.")
        if len(sms_body_value) > 160:
            raise HTTPException(status_code=400, detail="SMS messages must stay under 160 characters.")

    if channel_normalized in {"email", "both"}:
        logs.append(
            SubscriberNotificationLog(
                subscriber_id=prepared.subscriber.id,
                ticket_id=prepared.ticket.id if prepared.ticket else None,
                notification_id=None,
                channel=NotificationChannel.email,
                recipient=email_address or "",
                message_body=email_body_value,
                scheduled_for_at=send_at_utc,
                sent_by_user_id=sent_by_user_id,
                sent_by_person_id=sent_by_person_id,
            )
        )

    if channel_normalized in {"sms", "both"}:
        logs.append(
            SubscriberNotificationLog(
                subscriber_id=prepared.subscriber.id,
                ticket_id=prepared.ticket.id if prepared.ticket else None,
                notification_id=None,
                channel=NotificationChannel.sms,
                recipient=sms_number or "",
                message_body=sms_body_value,
                scheduled_for_at=send_at_utc,
                sent_by_user_id=sent_by_user_id,
                sent_by_person_id=sent_by_person_id,
            )
        )

    for log in logs:
        db.add(log)
    db.commit()
    for log in logs:
        db.refresh(log)
    return logs
