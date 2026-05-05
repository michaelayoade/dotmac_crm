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

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.domain_settings import SettingValueType
from app.models.event_store import EventStore
from app.models.integration import IntegrationTarget
from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.person import ChannelType, Person
from app.models.subscriber import Subscriber
from app.models.subscriber_notification import SubscriberNotificationLog
from app.models.tickets import Ticket, TicketComment, TicketPriority, TicketStatus
from app.schemas.settings import DomainSettingUpdate
from app.services import email as email_service
from app.services.domain_settings import notification_settings

DEDUPLICATE_WINDOW = timedelta(hours=6)
SEND_WINDOW_START_HOUR = 9
SEND_WINDOW_END_HOUR = 18
RECENT_ACTIVITY_WINDOW = timedelta(days=7)
TEMPLATE_SETTING_KEY = "subscriber_online_last_24h_templates"
TEST_NOTIFICATION_SUBSCRIBER_NUMBER = "TEST-NOTIFY-001"

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
    "issue_reference": "We are still tracking your open ticket",
    "escalated_formal": "We are still tracking your open ticket",
    "resolved_invite_back": "Following up after your resolved ticket",
}

EMAIL_TEMPLATES = {
    "friendly_check_in": """<!doctype html>
<html><body style="margin:0;padding:0;background:#f6f8fb;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:620px;margin:0 auto;padding:24px;">
    <div style="margin-bottom:18px;font-size:18px;font-weight:700;color:#111827;">dotmac</div>
    <div style="overflow:hidden;border-radius:24px;background:#ffffff;border:1px solid #e5e7eb;">
      <div style="padding:34px 38px;background:linear-gradient(135deg,#0f766e 0%,#34d399 100%);color:#ffffff;">
        <div style="display:inline-block;margin-bottom:14px;padding:6px 14px;border-radius:999px;border:1px solid rgba(255,255,255,.35);background:rgba(255,255,255,.18);font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;">Just Checking In</div>
        <h1 style="margin:0;font-size:27px;line-height:1.25;font-family:Georgia,serif;font-style:italic;">We noticed you have been away - just making sure all is well.</h1>
      </div>
      <div style="padding:36px 38px;color:#374151;">
        <p style="margin:0 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#9ca3af;">Hello,</p>
        <p style="margin:0 0 22px;font-size:22px;font-weight:700;color:#111827;">{name}</p>
        <p style="margin:0 0 18px;font-size:14.5px;line-height:1.8;">You have not been online in the past 24 hours and we have not heard from you. We just wanted to check in and make sure your service is running smoothly on your end.</p>
        <p style="margin:0 0 24px;font-size:14.5px;line-height:1.8;">Everything looks good from our side. Your connection is active and there are no reported issues on our network linked to your account.</p>
        <div style="display:flex;gap:16px;background:#f0fdf8;border:1.5px solid #bbf7d0;border-radius:16px;padding:18px 22px;margin-bottom:26px;">
          <div style="width:42px;height:42px;border-radius:50%;background:#0f766e;color:#fff;text-align:center;line-height:42px;font-weight:700;">OK</div>
          <div><div style="font-size:13px;font-weight:700;color:#0f766e;margin-bottom:4px;">Account Status: Active</div><div style="font-size:13px;color:#555;line-height:1.5;">Your service is live and no issues were detected on our end in the last 24 hours.</div></div>
        </div>
        <div style="background:#fff8f0;border-left:4px solid #fb923c;border-radius:0 14px 14px 0;padding:18px 22px;margin-bottom:28px;">
          <div style="font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#ea580c;margin-bottom:8px;">Need Something?</div>
          <p style="margin:0 0 8px;font-size:13.5px;line-height:1.6;color:#555;">Experiencing an issue? Open a new support ticket and our team will respond.</p>
          <p style="margin:0;font-size:13.5px;line-height:1.6;color:#555;">Have a question about your plan or billing? We are happy to help.</p>
        </div>
        <div style="text-align:center;margin-bottom:28px;"><a href="mailto:{support_email}" style="display:inline-block;background:#0f766e;color:#fff;text-decoration:none;font-size:14px;font-weight:700;padding:13px 30px;border-radius:999px;">Get Support</a></div>
        <div style="border-top:1px solid #efefef;padding-top:18px;text-align:center;font-size:12px;color:#9ca3af;line-height:1.7;">Questions? Email us at <a href="mailto:{support_email}" style="color:#6b7280;">{support_email}</a><br>&copy; 2026 Dotmac</div>
      </div>
    </div>
  </div>
</body></html>""",
    "issue_reference": """<!doctype html>
<html><body style="margin:0;padding:0;background:#f6f8fb;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:620px;margin:0 auto;padding:24px;">
    <div style="margin-bottom:18px;font-size:18px;font-weight:700;color:#111827;">dotmac</div>
    <div style="overflow:hidden;border-radius:24px;background:#ffffff;border:1px solid #e5e7eb;">
      <div style="padding:34px 38px;background:linear-gradient(135deg,#ff6b35 0%,#ff9a5c 100%);color:#ffffff;">
        <div style="display:inline-block;margin-bottom:14px;padding:6px 14px;border-radius:999px;border:1px solid rgba(255,255,255,.35);background:rgba(255,255,255,.18);font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;">Open Ticket</div>
        <h1 style="margin:0;font-size:27px;line-height:1.25;font-family:Georgia,serif;font-style:italic;">We noticed you have been away - and your ticket is still open.</h1>
      </div>
      <div style="padding:36px 38px;color:#374151;">
        <p style="margin:0 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#9ca3af;">Hello,</p>
        <p style="margin:0 0 22px;font-size:22px;font-weight:700;color:#111827;">{name}</p>
        <p style="margin:0 0 18px;font-size:14.5px;line-height:1.8;">We have not seen you online in the last 24 hours, and we noticed you still have an open support ticket. We want to make sure nothing falls through the cracks.</p>
        <p style="margin:0 0 24px;font-size:14.5px;line-height:1.8;">Our team is actively working on your issue. You do not need to do anything right now, but if your situation has changed or you have new information to share, we would love to hear from you.</p>
        <div style="border:1.5px solid #ffe5d6;border-radius:16px;padding:20px 22px;background:#fffaf7;margin-bottom:28px;">
          <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#ff6b35;margin-bottom:10px;">Your Open Ticket</div>
          <div style="font-size:13.5px;padding:7px 0;border-bottom:1px solid #f5ede7;"><span style="color:#888;">Ticket ID</span><span style="float:right;color:#111827;font-weight:700;">{ticket_id}</span></div>
          <div style="font-size:13.5px;padding:7px 0;border-bottom:1px solid #f5ede7;"><span style="color:#888;">Subject</span><span style="float:right;color:#111827;font-weight:700;">{ticket_subject}</span></div>
          <div style="font-size:13.5px;padding:7px 0;border-bottom:1px solid #f5ede7;"><span style="color:#888;">Opened</span><span style="float:right;color:#111827;font-weight:700;">{ticket_opened}</span></div>
          <div style="font-size:13.5px;padding:7px 0;"><span style="color:#888;">Status</span><span style="float:right;background:#fff3e0;color:#ff6b35;font-size:11px;font-weight:700;padding:3px 10px;border-radius:999px;">{ticket_status}</span></div>
        </div>
        <div style="text-align:center;margin-bottom:28px;"><a href="mailto:{support_email}" style="display:inline-block;background:#ff6b35;color:#fff;text-decoration:none;font-size:14px;font-weight:700;padding:13px 30px;border-radius:999px;">Reply to This Ticket</a></div>
        <div style="border-top:1px solid #efefef;padding-top:18px;text-align:center;font-size:12px;color:#9ca3af;line-height:1.7;">Questions? Email us at <a href="mailto:{support_email}" style="color:#6b7280;">{support_email}</a><br>&copy; 2026 Dotmac</div>
      </div>
    </div>
  </div>
</body></html>""",
    "escalated_formal": """<!doctype html>
<html><body style="margin:0;padding:0;background:#f6f8fb;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:620px;margin:0 auto;padding:24px;">
    <div style="margin-bottom:18px;font-size:18px;font-weight:700;color:#111827;">dotmac</div>
    <div style="overflow:hidden;border-radius:24px;background:#ffffff;border:1px solid #e5e7eb;">
      <div style="padding:34px 38px;background:linear-gradient(135deg,#ff6b35 0%,#ff9a5c 100%);color:#ffffff;">
        <div style="display:inline-block;margin-bottom:14px;padding:6px 14px;border-radius:999px;border:1px solid rgba(255,255,255,.35);background:rgba(255,255,255,.18);font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;">Open Ticket</div>
        <h1 style="margin:0;font-size:27px;line-height:1.25;font-family:Georgia,serif;font-style:italic;">We noticed you have been away - and your ticket is still open.</h1>
      </div>
      <div style="padding:36px 38px;color:#374151;">
        <p style="margin:0 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#9ca3af;">Hello,</p>
        <p style="margin:0 0 22px;font-size:22px;font-weight:700;color:#111827;">{name}</p>
        <p style="margin:0 0 18px;font-size:14.5px;line-height:1.8;">We have not seen you online in the last 24 hours, and your support ticket is still under active review. We want to make sure your case continues to get the right attention.</p>
        <p style="margin:0 0 24px;font-size:14.5px;line-height:1.8;">Our team is monitoring the latest activity: {last_activity}. If your situation has changed or you have new information to share, reply to this email and we will update the case.</p>
        <div style="border:1.5px solid #ffe5d6;border-radius:16px;padding:20px 22px;background:#fffaf7;margin-bottom:28px;">
          <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#ff6b35;margin-bottom:10px;">Your Open Ticket</div>
          <div style="font-size:13.5px;padding:7px 0;border-bottom:1px solid #f5ede7;"><span style="color:#888;">Ticket ID</span><span style="float:right;color:#111827;font-weight:700;">{ticket_id}</span></div>
          <div style="font-size:13.5px;padding:7px 0;border-bottom:1px solid #f5ede7;"><span style="color:#888;">Subject</span><span style="float:right;color:#111827;font-weight:700;">{ticket_subject}</span></div>
          <div style="font-size:13.5px;padding:7px 0;border-bottom:1px solid #f5ede7;"><span style="color:#888;">Opened</span><span style="float:right;color:#111827;font-weight:700;">{ticket_opened}</span></div>
          <div style="font-size:13.5px;padding:7px 0;"><span style="color:#888;">Status</span><span style="float:right;background:#fff3e0;color:#ff6b35;font-size:11px;font-weight:700;padding:3px 10px;border-radius:999px;">{ticket_status}</span></div>
        </div>
        <div style="text-align:center;margin-bottom:28px;"><a href="mailto:{support_email}" style="display:inline-block;background:#ff6b35;color:#fff;text-decoration:none;font-size:14px;font-weight:700;padding:13px 30px;border-radius:999px;">Reply to This Ticket</a></div>
        <div style="border-top:1px solid #efefef;padding-top:18px;text-align:center;font-size:12px;color:#9ca3af;line-height:1.7;">Questions? Email us at <a href="mailto:{support_email}" style="color:#6b7280;">{support_email}</a><br>&copy; 2026 Dotmac</div>
      </div>
    </div>
  </div>
</body></html>""",
    "resolved_invite_back": """<!doctype html>
<html><body style="margin:0;padding:0;background:#f6f8fb;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:620px;margin:0 auto;padding:24px;">
    <div style="margin-bottom:18px;font-size:18px;font-weight:700;color:#111827;">dotmac</div>
    <div style="overflow:hidden;border-radius:24px;background:#ffffff;border:1px solid #e5e7eb;">
      <div style="padding:34px 38px;background:linear-gradient(135deg,#2563eb 0%,#60a5fa 100%);color:#ffffff;">
        <div style="display:inline-block;margin-bottom:14px;padding:6px 14px;border-radius:999px;border:1px solid rgba(255,255,255,.35);background:rgba(255,255,255,.18);font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;">Closed Ticket</div>
        <h1 style="margin:0;font-size:27px;line-height:1.25;font-family:Georgia,serif;font-style:italic;">Your issue was resolved. Hope everything is working smoothly.</h1>
      </div>
      <div style="padding:36px 38px;color:#374151;">
        <p style="margin:0 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#9ca3af;">Hello,</p>
        <p style="margin:0 0 22px;font-size:22px;font-weight:700;color:#111827;">{name}</p>
        <p style="margin:0 0 18px;font-size:14.5px;line-height:1.8;">We noticed you have been offline for the past 24 hours. We also want to follow up on your recently closed support ticket - we hope the resolution was helpful and your service is running well.</p>
        <p style="margin:0 0 24px;font-size:14.5px;line-height:1.8;">Your experience matters to us. If something still feels off or the issue has come back, please reach out and we will get right on it.</p>
        <div style="border:1.5px solid #dbeafe;border-radius:16px;padding:20px 22px;background:#f8faff;margin-bottom:24px;">
          <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#2563eb;margin-bottom:10px;">Resolved Ticket</div>
          <div style="font-size:13.5px;padding:7px 0;border-bottom:1px solid #e8eef8;"><span style="color:#888;">Ticket ID</span><span style="float:right;color:#111827;font-weight:700;">{ticket_id}</span></div>
          <div style="font-size:13.5px;padding:7px 0;border-bottom:1px solid #e8eef8;"><span style="color:#888;">Subject</span><span style="float:right;color:#111827;font-weight:700;">{ticket_subject}</span></div>
          <div style="font-size:13.5px;padding:7px 0;border-bottom:1px solid #e8eef8;"><span style="color:#888;">Closed On</span><span style="float:right;color:#111827;font-weight:700;">{ticket_closed}</span></div>
          <div style="font-size:13.5px;padding:7px 0;"><span style="color:#888;">Status</span><span style="float:right;background:#e0f2e9;color:#16a34a;font-size:11px;font-weight:700;padding:3px 10px;border-radius:999px;">RESOLVED</span></div>
        </div>
        <div style="background:#fffbeb;border:1.5px solid #fde68a;border-radius:14px;padding:16px 20px;margin-bottom:28px;font-size:13.5px;color:#78350f;line-height:1.6;"><strong style="display:block;margin-bottom:4px;color:#92400e;">Still having issues?</strong>If the problem is not fully resolved, reply to this email or contact us at <strong>{support_email}</strong> and we will reopen your ticket immediately.</div>
        <div style="text-align:center;margin-bottom:28px;"><a href="mailto:{support_email}" style="display:inline-block;background:#2563eb;color:#fff;text-decoration:none;font-size:14px;font-weight:700;padding:13px 30px;border-radius:999px;">Contact Support</a></div>
        <div style="border-top:1px solid #efefef;padding-top:18px;text-align:center;font-size:12px;color:#9ca3af;line-height:1.7;">Questions? Email us at <a href="mailto:{support_email}" style="color:#6b7280;">{support_email}</a><br>&copy; 2026 Dotmac</div>
      </div>
    </div>
  </div>
</body></html>""",
}

WHATSAPP_TEMPLATES = {
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
class CampaignNotificationTemplate:
    subject: str
    body_text: str
    body_html: str | None = None
    template_key: str = ""


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


def _customer_first_name(person: Person | None, subscriber: Subscriber) -> str:
    if person is not None and person.first_name:
        return person.first_name.strip()
    return _display_name(person, subscriber)


def _format_last_seen(value: datetime | None, timezone_name: str) -> str:
    if value is None:
        return "recently"
    tz = _resolve_timezone(timezone_name)
    return value.astimezone(tz).strftime("%b %d, %Y %I:%M %p")


def _format_date(value: datetime | None, timezone_name: str) -> str:
    if value is None:
        return "-"
    tz = _resolve_timezone(timezone_name)
    return value.astimezone(tz).strftime("%b %d, %Y")


def _ticket_display_id(ticket: Ticket | None) -> str:
    if ticket is None:
        return "-"
    return ticket.number or ticket.erpnext_id or str(ticket.id)[:8]


def _ticket_status_label(ticket: Ticket | None) -> str:
    if ticket is None:
        return "-"
    return ticket.status.value.replace("_", " ").title()


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
            "sms_body": WHATSAPP_TEMPLATES[key],
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


def campaign_template_for_online_last_24h(db: Session, *, channel: str) -> CampaignNotificationTemplate:
    """Return the saved Notify template translated into campaign variables."""
    try:
        template = _load_template_map(db)["friendly_check_in"]
    except Exception:
        template = _default_template_map()["friendly_check_in"]
    body_key = "sms_body" if str(channel or "").strip().lower() == "whatsapp" else "email_body"
    body = template[body_key]
    replacements = {
        "{name}": "{{first_name}}",
        "{last_seen}": "recent activity",
        "{support_email}": "support@dotmac.ng",
        "{last_activity}": "recent account activity",
    }
    for token, value in replacements.items():
        body = body.replace(token, value)
    subject = template["email_subject"].replace("{support_email}", "support@dotmac.ng")
    body_html = _email_html_body(body) if str(channel or "").strip().lower() == "email" else None
    return CampaignNotificationTemplate(
        subject=subject,
        body_text=body,
        body_html=body_html,
        template_key="friendly_check_in",
    )


def render_online_last_24h_campaign_message(
    db: Session,
    *,
    subscriber_id: UUID,
    channel: str,
) -> CampaignNotificationTemplate:
    """Render the status-selected Notify template for a campaign recipient."""
    prepared = prepare_subscriber_notification(db, subscriber_id)
    selected_channel = str(channel or "").strip().lower()
    subject = _render_template(prepared.template.email_subject, prepared.token_values)
    if selected_channel == "whatsapp":
        body = _render_template(prepared.template.sms_body, prepared.token_values)
        return CampaignNotificationTemplate(
            subject=subject,
            body_text=body,
            body_html=None,
            template_key=prepared.template.template_key,
        )
    body = _render_template(prepared.template.email_body, prepared.token_values)
    return CampaignNotificationTemplate(
        subject=subject,
        body_text=body,
        body_html=_email_html_body(body),
        template_key=prepared.template.template_key,
    )


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


def _preferred_whatsapp_number(person: Person | None) -> str | None:
    if person is None:
        return None

    def _normalize(value: str | None) -> str | None:
        if not value:
            return None
        text = value.strip()
        digits = re.sub(r"\D+", "", text)
        if not digits:
            return None
        if digits.startswith("0") and len(digits) == 11:
            digits = f"234{digits[1:]}"
        if len(digits) < 8 or len(digits) > 15:
            return None
        return f"+{digits}"

    channels = getattr(person, "channels", None) or []
    for preferred_type in (ChannelType.whatsapp, ChannelType.phone, ChannelType.sms):
        for channel in channels:
            if channel.channel_type == preferred_type and channel.address:
                normalized = _normalize(str(channel.address))
                if normalized:
                    return normalized
    if person.phone:
        return _normalize(str(person.phone))
    return None


def _default_outreach_target(db: Session, connector_type: ConnectorType) -> IntegrationTarget | None:
    return (
        db.query(IntegrationTarget)
        .join(ConnectorConfig, IntegrationTarget.connector_config_id == ConnectorConfig.id)
        .filter(IntegrationTarget.is_active.is_(True))
        .filter(ConnectorConfig.is_active.is_(True))
        .filter(ConnectorConfig.connector_type == connector_type)
        .order_by(IntegrationTarget.created_at.asc())
        .first()
    )


def prepare_subscriber_notification(db: Session, subscriber_id: UUID) -> PreparedSubscriberNotification:
    subscriber = db.get(Subscriber, subscriber_id)
    if subscriber is None or not subscriber.is_active:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    person = db.get(Person, subscriber.person_id) if subscriber.person_id else None
    ticket = _latest_ticket_for_subscriber(db, subscriber.id)
    timezone_name = (person.timezone if person and person.timezone else "UTC").strip() or "UTC"
    support_email = "support@dotmac.ng"
    token_values = {
        "{name}": _customer_first_name(person, subscriber),
        "{last_seen}": "recently",
        "{support_email}": str(support_email),
        "{last_activity}": "recent account activity",
        "{ticket_id}": _ticket_display_id(ticket),
        "{ticket_subject}": ticket.title if ticket else "-",
        "{ticket_opened}": _format_date(ticket.created_at if ticket else None, timezone_name),
        "{ticket_closed}": _format_date((ticket.closed_at or ticket.resolved_at) if ticket else None, timezone_name),
        "{ticket_status}": _ticket_status_label(ticket),
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
                Notification.created_at.label("notification_created_at"),
                Notification.sent_at.label("notification_sent_at"),
            )
            .join(
                Notification,
                Notification.id == SubscriberNotificationLog.notification_id,
                isouter=True,
            )
            .where(SubscriberNotificationLog.subscriber_id.in_(subscriber_ids))
            .order_by(SubscriberNotificationLog.subscriber_id.asc(), SubscriberNotificationLog.created_at.desc())
        )
        for log, notification_status, notification_created_at, notification_sent_at in db.execute(ranked_logs).all():
            subscriber_key = str(log.subscriber_id)
            if subscriber_key in latest_logs:
                continue
            log._notification_status_value = notification_status.value if notification_status else "testing_hold"
            log._notification_sent_at = notification_sent_at or notification_created_at if notification_status else None
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
            sent_at = getattr(latest_log, "_notification_sent_at", None)
            if sent_at is not None:
                row["latest_notification_sent_for"] = _format_last_seen(sent_at, prepared.timezone_name)
                row["latest_notification_sent_status"] = row["latest_notification_status"]
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
        body=_email_html_body(body) if channel == NotificationChannel.email else body,
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
    if prepared.subscriber.subscriber_number != TEST_NOTIFICATION_SUBSCRIBER_NUMBER and _recent_notification_exists(
        db, prepared.subscriber.id
    ):
        raise HTTPException(
            status_code=409, detail="A notification was already sent to this customer in the last 6 hours."
        )
    if sent_by_person_id and db.get(Person, sent_by_person_id) is None:
        sent_by_person_id = None

    channel_normalized = (channel_value or "").strip().lower()
    if channel_normalized not in {"email", "whatsapp", "both"}:
        raise HTTPException(status_code=400, detail="Invalid notification channel.")

    send_at_utc, _display_local = _effective_send_at(prepared.timezone_name, scheduled_local_text)
    logs: list[SubscriberNotificationLog] = []
    email_address = prepared.person.email.strip() if prepared.person and prepared.person.email else None
    whatsapp_number = _preferred_whatsapp_number(prepared.person)
    email_subject_value = (email_subject or "").strip()
    email_body_value = (email_body or "").strip()
    whatsapp_body_value = (sms_body or "").strip()

    if channel_normalized in {"email", "both"}:
        if not email_address:
            raise HTTPException(status_code=400, detail="Customer does not have an email address.")
        if not email_subject_value:
            raise HTTPException(status_code=400, detail="Email subject is required.")
        if not email_body_value:
            raise HTTPException(status_code=400, detail="Email body is required.")
        if not _is_html_body(email_body_value) and _count_sentences(email_body_value) > 4:
            raise HTTPException(status_code=400, detail="Emails must stay within 3 to 4 short sentences.")

    if channel_normalized in {"whatsapp", "both"}:
        if not whatsapp_number:
            raise HTTPException(status_code=400, detail="Customer does not have a WhatsApp number.")
        if not whatsapp_body_value:
            raise HTTPException(status_code=400, detail="WhatsApp body is required.")

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

    if channel_normalized in {"whatsapp", "both"}:
        logs.append(
            SubscriberNotificationLog(
                subscriber_id=prepared.subscriber.id,
                ticket_id=prepared.ticket.id if prepared.ticket else None,
                notification_id=None,
                channel=NotificationChannel.whatsapp,
                recipient=whatsapp_number or "",
                message_body=whatsapp_body_value,
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


def queue_bulk_subscriber_notifications(
    db: Session,
    *,
    subscriber_ids: list[UUID],
    channel_value: str,
    email_subject: str | None,
    email_body: str | None,
    sms_body: str | None,
    scheduled_local_text: str | None,
    sent_by_user_id: UUID | None,
    sent_by_person_id: UUID | None,
) -> dict[str, int]:
    """Queue notifications for multiple subscribers using per-subscriber token rendering."""
    queued = 0
    skipped = 0
    seen: set[UUID] = set()
    for subscriber_id in subscriber_ids:
        if subscriber_id in seen:
            continue
        seen.add(subscriber_id)
        try:
            prepared = prepare_subscriber_notification(db, subscriber_id)
            rendered_subject = _render_template(email_subject or prepared.template.email_subject, prepared.token_values)
            rendered_email = _render_template(email_body or prepared.template.email_body, prepared.token_values)
            rendered_sms = _render_template(sms_body or prepared.template.sms_body, prepared.token_values)
            logs = queue_subscriber_notification(
                db,
                subscriber_id=subscriber_id,
                channel_value=channel_value,
                email_subject=rendered_subject,
                email_body=rendered_email,
                sms_body=rendered_sms,
                scheduled_local_text=scheduled_local_text,
                sent_by_user_id=sent_by_user_id,
                sent_by_person_id=sent_by_person_id,
            )
            queued += len(logs)
        except HTTPException:
            db.rollback()
            skipped += 1
    return {"queued": queued, "skipped": skipped, "selected": len(seen)}


def approve_and_send_test_notifications(
    db: Session,
    *,
    subscriber_id: UUID,
    approved_by_person_id: UUID | None,
) -> dict[str, int]:
    """Send queued preview notifications for the local test account only."""
    subscriber = db.get(Subscriber, subscriber_id)
    if subscriber is None or subscriber.subscriber_number != TEST_NOTIFICATION_SUBSCRIBER_NUMBER:
        raise HTTPException(status_code=403, detail="Approve & Send is only enabled for the test account.")
    if subscriber.person_id is None:
        raise HTTPException(status_code=400, detail="Test subscriber is not linked to a contact.")
    prepared = prepare_subscriber_notification(db, subscriber.id)
    logs = (
        db.query(SubscriberNotificationLog)
        .filter(SubscriberNotificationLog.subscriber_id == subscriber.id)
        .filter(SubscriberNotificationLog.notification_id.is_(None))
        .order_by(SubscriberNotificationLog.created_at.asc())
        .all()
    )
    if not logs:
        raise HTTPException(status_code=404, detail="No queued preview notifications found for the test account.")

    sent = 0
    failed = 0
    for log in logs:
        notification = _create_notification(
            db=db,
            channel=log.channel,
            recipient=log.recipient,
            send_at=datetime.now(UTC),
            subject=prepared.template.email_subject if log.channel == NotificationChannel.email else None,
            body=log.message_body,
        )
        db.flush()
        log.notification_id = notification.id
        if _send_test_notification_log(
            db,
            log=log,
            notification=notification,
            prepared=prepared,
            approved_by_person_id=approved_by_person_id,
        ):
            sent += 1
        else:
            failed += 1
    db.commit()
    return {"sent": sent, "failed": failed, "selected": len(logs)}


def _send_test_notification_log(
    db: Session,
    *,
    log: SubscriberNotificationLog,
    notification: Notification,
    prepared: PreparedSubscriberNotification,
    approved_by_person_id: UUID | None,
) -> bool:
    notification.status = NotificationStatus.sending
    if log.channel == NotificationChannel.email:
        return _send_test_email_log(db, log=log, notification=notification, prepared=prepared)
    if log.channel == NotificationChannel.whatsapp:
        return _send_test_whatsapp_log(
            db,
            log=log,
            notification=notification,
            prepared=prepared,
            approved_by_person_id=approved_by_person_id,
        )
    notification.status = NotificationStatus.failed
    notification.last_error = f"Unsupported channel for test send: {log.channel.value}"
    return False


def _send_test_email_log(
    db: Session,
    *,
    log: SubscriberNotificationLog,
    notification: Notification,
    prepared: PreparedSubscriberNotification,
) -> bool:
    html_body = _email_html_body(log.message_body)
    sent, debug = email_service.send_email(
        db,
        log.recipient,
        prepared.template.email_subject,
        html_body,
        log.message_body,
        track=False,
        from_email="support@dotmac.ng",
        reply_to="support@dotmac.ng",
    )
    notification.status = NotificationStatus.delivered if sent else NotificationStatus.failed
    if debug:
        notification.last_error = str(debug)
    return bool(sent)


def _email_html_body(message_body: str) -> str:
    body = (message_body or "").strip()
    if not body:
        return ""
    if _is_html_body(body):
        return body
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", body) if part.strip()]
    if not paragraphs:
        paragraphs = [body]
    paragraph_html = "\n".join(
        f'<p style="margin:0 0 14px;color:#1f2937;font-size:15px;line-height:1.6;">'
        f"{escape(paragraph).replace(chr(10), '<br>')}</p>"
        for paragraph in paragraphs
    )
    return f"""<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f6f8fb;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:640px;margin:0 auto;padding:24px;">
    <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:14px;padding:24px;">
      <div style="font-size:13px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:#0f766e;margin-bottom:18px;">Dotmac Fiber</div>
      {paragraph_html}
    </div>
  </div>
</body>
</html>"""


def _is_html_body(body: str) -> bool:
    return bool(re.search(r"</?(?:p|div|br|table|ul|ol|li|strong|span|html|body|a)\b", body, flags=re.IGNORECASE))


def _send_test_whatsapp_log(
    db: Session,
    *,
    log: SubscriberNotificationLog,
    notification: Notification,
    prepared: PreparedSubscriberNotification,
    approved_by_person_id: UUID | None,
) -> bool:
    try:
        from app.services.crm.web_campaigns import create_billing_risk_outreach_campaign, send_campaign_now

        target = _default_outreach_target(db, ConnectorType.whatsapp)
        if target is None:
            raise HTTPException(status_code=400, detail="No active WhatsApp outreach target is configured.")

        campaign = create_billing_risk_outreach_campaign(
            db,
            name="Online Last 24H Test Outreach",
            channel="whatsapp",
            channel_target_id=str(target.id),
            subscriber_ids=[str(prepared.subscriber.id)],
            retention_customer_ids=[str(prepared.subscriber.id)],
            created_by_id=str(approved_by_person_id) if approved_by_person_id else None,
            source_filters={
                "source_report": "online_last_24h",
                "test_account": True,
                "subscriber_notification_log_id": str(log.id),
            },
        )
        campaign.subject = "Online Last 24H Test Outreach"
        campaign.body_text = log.message_body
        campaign.connector_config_id = target.connector_config_id
        metadata = dict(campaign.metadata_ or {})
        metadata["subscriber_notification_log_id"] = str(log.id)
        metadata["subscriber_notification_id"] = str(notification.id)
        campaign.metadata_ = metadata
        db.flush()

        send_campaign_now(db, campaign_id=str(campaign.id))
        notification.status = NotificationStatus.queued
        notification.last_error = None
        return True
    except Exception as exc:
        notification.status = NotificationStatus.failed
        notification.last_error = str(exc)
        return False
