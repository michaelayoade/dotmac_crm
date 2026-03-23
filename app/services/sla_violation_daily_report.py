from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.domain_settings import DomainSetting, SettingDomain, SettingValueType
from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember
from app.services import email as email_service

REPORT_TIMEZONE = "Africa/Lagos"
REPORT_TEAM_NAME = "Customer Experience"
REPORT_SUBJECT = "SLA Violation Report"
REPORT_LAST_SENT_KEY = "sla_violation_daily_report_last_sent_on"
REPORT_HEADERS = [
    "region",
    "entity_type",
    "reference",
    "title",
    "project",
    "sla_status",
    "breached_at",
    "time_over_target",
    "detail_url",
]


def _format_breached_at(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    local_value = value.astimezone(ZoneInfo(REPORT_TIMEZONE))
    return local_value.strftime("%Y-%m-%d %H:%M:%S %Z")


def _csv_rows(records: list[dict]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for record in records:
        rows.append(
            {
                "region": str(record.get("region") or ""),
                "entity_type": str(record.get("entity_type") or ""),
                "reference": str(record.get("reference") or record.get("id") or ""),
                "title": str(record.get("title") or ""),
                "project": str(record.get("project") or ""),
                "sla_status": str(record.get("sla_status") or record.get("status") or ""),
                "breached_at": _format_breached_at(record.get("breached_at")),
                "time_over_target": str(record.get("time_over_target") or record.get("breach_duration") or ""),
                "detail_url": str(record.get("detail_url") or ""),
            }
        )
    return rows


class SlaViolationDailyReportService:
    @staticmethod
    def list_recipient_emails(db: Session) -> list[str]:
        rows = (
            db.query(Person.email)
            .join(ServiceTeamMember, ServiceTeamMember.person_id == Person.id)
            .join(ServiceTeam, ServiceTeam.id == ServiceTeamMember.team_id)
            .filter(ServiceTeam.is_active.is_(True))
            .filter(ServiceTeamMember.is_active.is_(True))
            .filter(Person.is_active.is_(True))
            .filter(ServiceTeam.name == REPORT_TEAM_NAME)
            .filter(Person.email.isnot(None))
            .order_by(Person.email.asc())
            .distinct()
            .all()
        )
        return [str(email).strip() for (email,) in rows if email and str(email).strip()]

    @staticmethod
    def get_open_violation_records(db: Session, *, entity_type: str) -> list[dict]:
        from app.services.operations_sla_reports import operations_sla_violations_report

        return operations_sla_violations_report.list_records(
            db,
            entity_type=entity_type,  # type: ignore[arg-type]
            region=None,
            start_at=None,
            end_at=None,
            limit=1000,
            open_only=True,
        )

    @staticmethod
    def build_csv_content(db: Session) -> str:
        ticket_rows = _csv_rows(SlaViolationDailyReportService.get_open_violation_records(db, entity_type="ticket"))
        project_rows = _csv_rows(SlaViolationDailyReportService.get_open_violation_records(db, entity_type="project"))

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(["Tickets"])
        writer.writerow(REPORT_HEADERS)
        for row in ticket_rows:
            writer.writerow([row[header] for header in REPORT_HEADERS])

        writer.writerow([])
        writer.writerow(["Projects"])
        writer.writerow(REPORT_HEADERS)
        for row in project_rows:
            writer.writerow([row[header] for header in REPORT_HEADERS])

        return output.getvalue()

    @staticmethod
    def build_attachment(db: Session, *, report_date: str) -> dict[str, str]:
        return {
            "filename": f"sla-violation-report-{report_date}.csv",
            "content": SlaViolationDailyReportService.build_csv_content(db),
            "content_type": "text/csv",
        }

    @staticmethod
    def build_email_body(*, report_date: str) -> str:
        return (
            "<p>Please find attached the daily SLA violation report.</p>"
            f"<p>Report date: {report_date} ({REPORT_TIMEZONE}).</p>"
            "<p>The attachment contains separate sections for Tickets and Projects, grouped with a region column.</p>"
        )

    @staticmethod
    def get_last_sent_business_date(db: Session) -> str | None:
        setting = (
            db.query(DomainSetting)
            .filter(DomainSetting.domain == SettingDomain.notification)
            .filter(DomainSetting.key == REPORT_LAST_SENT_KEY)
            .filter(DomainSetting.is_active.is_(True))
            .first()
        )
        if not setting:
            return None
        return (setting.value_text or "").strip() or None

    @staticmethod
    def set_last_sent_business_date(db: Session, business_date: str) -> None:
        setting = (
            db.query(DomainSetting)
            .filter(DomainSetting.domain == SettingDomain.notification)
            .filter(DomainSetting.key == REPORT_LAST_SENT_KEY)
            .first()
        )
        if setting is None:
            setting = DomainSetting(
                domain=SettingDomain.notification,
                key=REPORT_LAST_SENT_KEY,
                value_type=SettingValueType.string,
                value_text=business_date,
                is_active=True,
            )
            db.add(setting)
        else:
            setting.value_type = SettingValueType.string
            setting.value_text = business_date
            setting.value_json = None
            setting.is_active = True
        db.commit()

    @staticmethod
    def send_daily_report(db: Session, *, report_date: str) -> tuple[bool, dict | None]:
        recipients = SlaViolationDailyReportService.list_recipient_emails(db)
        if not recipients:
            return False, {"error": f"No active recipients found for team {REPORT_TEAM_NAME}"}

        primary = recipients[0]
        bcc_emails = recipients[1:] or None
        attachment = SlaViolationDailyReportService.build_attachment(db, report_date=report_date)
        return email_service.send_email(
            db=db,
            to_email=primary,
            subject=REPORT_SUBJECT,
            body_html=SlaViolationDailyReportService.build_email_body(report_date=report_date),
            body_text=None,
            track=False,
            bcc_emails=bcc_emails,
            attachments=[attachment],
        )


sla_violation_daily_report_service = SlaViolationDailyReportService()
