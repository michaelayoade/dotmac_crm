from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from email.utils import parseaddr
from html import escape as html_escape
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingValueType
from app.models.scheduler import ScheduledTask, ScheduleType
from app.schemas.settings import DomainSettingUpdate
from app.services import email as email_service
from app.services import settings_spec
from app.services.domain_settings import notification_settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Africa/Lagos"
DEFAULT_LOCAL_TIME = "08:00"
DEFAULT_SUBJECT = "Weekly NCC Report"
DEFAULT_BODY_TEMPLATE = (
    "Please find attached the NCC report for the last {lookback_days} day(s).\nRows included: {row_count}."
)
DEFAULT_SEND_DAY = "monday"
DEFAULT_LOOKBACK_DAYS = 7
NCC_REPORT_EMAIL_TASK_NAME = "app.tasks.reports.send_scheduled_ncc_report"
NCC_REPORT_EMAIL_LAST_SENT_KEY = "ncc_report_email_last_sent_local_date"
WEEKDAY_OPTIONS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass(frozen=True)
class NccReportEmailConfig:
    enabled: bool
    recipient_email: str
    cc_emails: list[str]
    bcc_emails: list[str]
    from_name: str | None
    subject: str
    body_template: str
    local_time: str
    timezone: str
    send_day: str
    lookback_days: int
    last_sent_local_date: str | None


def _coerce_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _resolve_setting(db: Session, key: str) -> object | None:
    domain = notification_settings.domain
    if domain is None:
        raise RuntimeError("Notification settings domain is not configured")
    return settings_spec.resolve_value(db, domain, key)


def _parse_local_time(value: str | None) -> time:
    text = str(value or DEFAULT_LOCAL_TIME).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.time().replace(second=0, microsecond=0)
        except ValueError:
            continue
    raise ValueError("Enter a valid send time in HH:MM format.")


def _resolve_timezone(value: str | None) -> ZoneInfo:
    timezone_value = str(value or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(timezone_value)
    except Exception as exc:
        raise ValueError(f"Unknown timezone: {timezone_value}") from exc


def _normalize_send_day(value: str | None) -> str:
    send_day = str(value or DEFAULT_SEND_DAY).strip().lower()
    if send_day not in WEEKDAY_OPTIONS:
        raise ValueError("Select a valid send day.")
    return send_day


def _split_emails(value: str | None) -> list[str]:
    raw = str(value or "").replace(";", ",")
    emails: list[str] = []
    for part in raw.split(","):
        parsed = parseaddr(part.strip())[1].strip()
        if parsed and "@" in parsed and parsed not in emails:
            emails.append(parsed)
    return emails


def _validate_primary_email(value: str | None) -> str:
    parsed = parseaddr(str(value or "").strip())[1].strip()
    if not parsed or "@" not in parsed:
        raise ValueError("Enter a recipient email address.")
    return parsed


def _setting_payload(
    *,
    value_type: SettingValueType,
    value_text: str | None = None,
    value_json: dict | list | bool | int | str | None = None,
) -> DomainSettingUpdate:
    return DomainSettingUpdate(value_type=value_type, value_text=value_text, value_json=value_json, is_active=True)


def _load_config(db: Session) -> NccReportEmailConfig:
    return NccReportEmailConfig(
        enabled=_coerce_bool(_resolve_setting(db, "ncc_report_email_enabled"), False),
        recipient_email=str(_resolve_setting(db, "ncc_report_email_to") or "").strip(),
        cc_emails=_split_emails(str(_resolve_setting(db, "ncc_report_email_cc") or "")),
        bcc_emails=_split_emails(str(_resolve_setting(db, "ncc_report_email_bcc") or "")),
        from_name=str(_resolve_setting(db, "ncc_report_email_from_name") or "").strip() or None,
        subject=str(_resolve_setting(db, "ncc_report_email_subject") or DEFAULT_SUBJECT).strip() or DEFAULT_SUBJECT,
        body_template=str(_resolve_setting(db, "ncc_report_email_body_template") or DEFAULT_BODY_TEMPLATE).strip()
        or DEFAULT_BODY_TEMPLATE,
        local_time=str(_resolve_setting(db, "ncc_report_email_local_time") or DEFAULT_LOCAL_TIME).strip()
        or DEFAULT_LOCAL_TIME,
        timezone=str(_resolve_setting(db, "ncc_report_email_timezone") or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE,
        send_day=_normalize_send_day(str(_resolve_setting(db, "ncc_report_email_send_day") or DEFAULT_SEND_DAY)),
        lookback_days=max(
            _coerce_int(_resolve_setting(db, "ncc_report_email_lookback_days"), DEFAULT_LOOKBACK_DAYS), 1
        ),
        last_sent_local_date=str(_resolve_setting(db, NCC_REPORT_EMAIL_LAST_SENT_KEY) or "").strip() or None,
    )


def get_settings_snapshot(db: Session) -> dict[str, Any]:
    config = _load_config(db)
    return {
        "enabled": config.enabled,
        "recipient_email": config.recipient_email,
        "cc": ", ".join(config.cc_emails),
        "bcc": ", ".join(config.bcc_emails),
        "from_name": config.from_name or "",
        "subject": config.subject,
        "body_template": config.body_template,
        "local_time": config.local_time,
        "timezone": config.timezone,
        "send_day": config.send_day,
        "send_day_options": list(WEEKDAY_OPTIONS.keys()),
        "lookback_days": config.lookback_days,
        "last_sent_local_date": config.last_sent_local_date or "",
    }


def _sync_ncc_report_scheduled_task(db: Session, *, enabled: bool) -> None:
    task = (
        db.query(ScheduledTask)
        .filter(ScheduledTask.task_name == NCC_REPORT_EMAIL_TASK_NAME)
        .order_by(ScheduledTask.created_at.desc())
        .first()
    )
    if task is None:
        if not enabled:
            return
        task = ScheduledTask(
            name="ncc_report_email",
            task_name=NCC_REPORT_EMAIL_TASK_NAME,
            schedule_type=ScheduleType.interval,
            interval_seconds=300,
            enabled=True,
        )
        db.add(task)
        db.commit()
        return

    changed = False
    if task.name != "ncc_report_email":
        task.name = "ncc_report_email"
        changed = True
    if task.interval_seconds != 300:
        task.interval_seconds = 300
        changed = True
    if task.enabled != enabled:
        task.enabled = enabled
        changed = True
    if changed:
        db.commit()


def save_email_settings(
    db: Session,
    *,
    enabled: bool,
    recipient_email: str,
    cc: str,
    bcc: str,
    from_name: str,
    subject: str,
    body_template: str,
    local_time: str,
    timezone: str,
    lookback_days: int,
    send_day: str = DEFAULT_SEND_DAY,
) -> dict[str, Any]:
    primary = _validate_primary_email(recipient_email) if enabled else parseaddr(str(recipient_email or ""))[1].strip()
    parsed_time = _parse_local_time(local_time)
    timezone_value = str(timezone or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    _resolve_timezone(timezone_value)
    send_day_value = _normalize_send_day(send_day)
    lookback_value = min(max(_coerce_int(lookback_days, DEFAULT_LOOKBACK_DAYS), 1), 366)
    from_name_value = str(from_name or "").strip()
    subject_value = str(subject or DEFAULT_SUBJECT).strip() or DEFAULT_SUBJECT
    body_template_value = str(body_template or DEFAULT_BODY_TEMPLATE).strip() or DEFAULT_BODY_TEMPLATE
    cc_values = _split_emails(cc)
    bcc_values = _split_emails(bcc)

    notification_settings.upsert_by_key(
        db,
        "ncc_report_email_enabled",
        _setting_payload(
            value_type=SettingValueType.boolean,
            value_text="true" if enabled else "false",
            value_json=enabled,
        ),
    )
    notification_settings.upsert_by_key(
        db,
        "ncc_report_email_to",
        _setting_payload(value_type=SettingValueType.string, value_text=primary),
    )
    notification_settings.upsert_by_key(
        db,
        "ncc_report_email_cc",
        _setting_payload(value_type=SettingValueType.string, value_text=", ".join(cc_values)),
    )
    notification_settings.upsert_by_key(
        db,
        "ncc_report_email_bcc",
        _setting_payload(value_type=SettingValueType.string, value_text=", ".join(bcc_values)),
    )
    notification_settings.upsert_by_key(
        db,
        "ncc_report_email_from_name",
        _setting_payload(value_type=SettingValueType.string, value_text=from_name_value),
    )
    notification_settings.upsert_by_key(
        db,
        "ncc_report_email_subject",
        _setting_payload(value_type=SettingValueType.string, value_text=subject_value),
    )
    notification_settings.upsert_by_key(
        db,
        "ncc_report_email_body_template",
        _setting_payload(value_type=SettingValueType.string, value_text=body_template_value),
    )
    notification_settings.upsert_by_key(
        db,
        "ncc_report_email_local_time",
        _setting_payload(value_type=SettingValueType.string, value_text=parsed_time.strftime("%H:%M")),
    )
    notification_settings.upsert_by_key(
        db,
        "ncc_report_email_timezone",
        _setting_payload(value_type=SettingValueType.string, value_text=timezone_value),
    )
    notification_settings.upsert_by_key(
        db,
        "ncc_report_email_send_day",
        _setting_payload(value_type=SettingValueType.string, value_text=send_day_value),
    )
    notification_settings.upsert_by_key(
        db,
        "ncc_report_email_lookback_days",
        _setting_payload(value_type=SettingValueType.integer, value_text=str(lookback_value)),
    )
    _sync_ncc_report_scheduled_task(db, enabled=enabled)
    return get_settings_snapshot(db)


def _mark_sent(db: Session, run_local_date: str) -> None:
    notification_settings.upsert_by_key(
        db,
        NCC_REPORT_EMAIL_LAST_SENT_KEY,
        _setting_payload(value_type=SettingValueType.string, value_text=run_local_date),
    )


class _PlaceholderValues(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render_body_template(
    template: str,
    *,
    lookback_days: int,
    row_count: int,
    report_date: str,
) -> str:
    values = _PlaceholderValues(
        lookback_days=str(lookback_days),
        row_count=str(row_count),
        report_date=report_date,
    )
    return (template or DEFAULT_BODY_TEMPLATE).format_map(values).strip() or DEFAULT_BODY_TEMPLATE.format_map(values)


def _text_to_html(text: str) -> str:
    escaped = html_escape(text).replace("\n", "<br>")
    return f"<p>{escaped}</p>"


def run_scheduled_ncc_report_email(db: Session, *, now_utc: datetime | None = None) -> dict[str, Any]:
    config = _load_config(db)
    if not config.enabled:
        return {"status": "skipped", "reason": "disabled"}
    if not config.recipient_email:
        return {"status": "skipped", "reason": "missing_recipient"}

    zone = _resolve_timezone(config.timezone)
    scheduled_time = _parse_local_time(config.local_time)
    now = now_utc or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    local_now = now.astimezone(zone)
    run_local_date = local_now.date().isoformat()
    if local_now.weekday() != WEEKDAY_OPTIONS[config.send_day]:
        return {
            "status": "skipped",
            "reason": "not_scheduled_day",
            "send_day": config.send_day,
            "run_local_date": run_local_date,
        }
    if local_now.time().replace(second=0, microsecond=0) < scheduled_time:
        return {"status": "skipped", "reason": "before_scheduled_time"}
    if config.last_sent_local_date == run_local_date:
        return {"status": "skipped", "reason": "already_sent", "run_local_date": run_local_date}

    end_dt = now.astimezone(UTC)
    start_dt = end_dt - timedelta(days=config.lookback_days)
    from app.web.admin import reports as ncc_reports

    records = ncc_reports._ncc_export_rows(ncc_reports._build_ncc_records(db, start_dt, end_dt))
    workbook = ncc_reports._build_ncc_workbook(records, ncc_reports._NCC_COLUMNS)
    attachment = {
        "file_name": ncc_reports._NCC_EXPORT_FILENAME,
        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "content_base64": base64.b64encode(workbook).decode("ascii"),
    }
    body_text = _render_body_template(
        config.body_template,
        lookback_days=config.lookback_days,
        row_count=len(records),
        report_date=run_local_date,
    )
    ok, debug = email_service.send_email(
        db=db,
        to_email=config.recipient_email,
        subject=config.subject,
        body_html=_text_to_html(body_text),
        body_text=body_text,
        track=False,
        from_name=config.from_name,
        cc_emails=config.cc_emails or None,
        bcc_emails=config.bcc_emails or None,
        attachments=[attachment],
    )
    if not ok:
        return {"status": "failed", "error": (debug or {}).get("error", "Email send failed")}

    _mark_sent(db, run_local_date)
    logger.info("NCC report email sent to %s rows=%s", config.recipient_email, len(records))
    return {
        "status": "sent",
        "rows": len(records),
        "recipient": config.recipient_email,
        "run_local_date": run_local_date,
    }
