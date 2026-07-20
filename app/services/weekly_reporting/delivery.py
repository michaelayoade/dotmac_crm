"""Email delivery for validated weekly reporting artifacts."""

from __future__ import annotations

import base64
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.services import email as email_service
from app.services.weekly_reporting.configuration import WeeklyReportingConfig

SALES_PDF_NAME = "Weekly_Sales_Inbound_Experience_Report.pdf"
SUPPORT_PDF_NAME = "Weekly_Support_Inbound_Experience_Report.pdf"


def _attachment(path: Path) -> dict[str, str]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"Required PDF report is missing or empty: {path.name}")
    return {
        "file_name": path.name,
        "mime_type": "application/pdf",
        "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


def _email_content(
    *,
    reporting_period: str,
    generated_at: datetime,
    timezone: str,
    summary: dict[str, Any],
) -> tuple[str, str, str]:
    local_generated_at = generated_at.astimezone(ZoneInfo(timezone))
    generated_label = local_generated_at.strftime("%d %B %Y at %H:%M %Z")
    subject = f"DotMac Weekly Inbound Experience Reports | {reporting_period}"
    body_text = (
        "Hello,\n\n"
        "Please find attached the approved DotMac weekly inbound experience reports.\n\n"
        f"Reporting period: {reporting_period}\n"
        f"Generated: {generated_label} ({timezone})\n\n"
        "Attached reports:\n"
        "- Weekly Sales Inbound Experience Report\n"
        "- Weekly Support Inbound Experience Report\n\n"
        "Execution summary:\n"
        f"- Inbound conversations analysed: {summary['conversations_analysed']}\n"
        f"- Sales conversations identified: {summary['sales_conversations']}\n"
        f"- Support conversations identified: {summary['support_conversations']}\n"
        f"- Active inboxes processed: {summary['active_inboxes']}\n\n"
        "Both reports completed validation before this email was sent.\n\n"
        "Regards,\n"
        "DotMac Omni Weekly Reporting Engine"
    )
    body_html = (
        "<p>Hello,</p>"
        "<p>Please find attached the approved DotMac weekly inbound experience reports.</p>"
        f"<p><strong>Reporting period:</strong> {escape(reporting_period)}<br>"
        f"<strong>Generated:</strong> {escape(generated_label)} ({escape(timezone)})</p>"
        "<p><strong>Attached reports:</strong></p>"
        "<ul><li>Weekly Sales Inbound Experience Report</li>"
        "<li>Weekly Support Inbound Experience Report</li></ul>"
        "<p><strong>Execution summary:</strong></p>"
        f"<ul><li>Inbound conversations analysed: {summary['conversations_analysed']}</li>"
        f"<li>Sales conversations identified: {summary['sales_conversations']}</li>"
        f"<li>Support conversations identified: {summary['support_conversations']}</li>"
        f"<li>Active inboxes processed: {summary['active_inboxes']}</li></ul>"
        "<p>Both reports completed validation before this email was sent.</p>"
        "<p>Regards,<br>DotMac Omni Weekly Reporting Engine</p>"
    )
    return subject, body_text, body_html


def deliver_reports(
    db: Session,
    *,
    config: WeeklyReportingConfig,
    archive_dir: Path,
    reporting_period: str,
    generated_at: datetime,
    summary: dict[str, Any],
) -> dict[str, Any]:
    if not config.recipients:
        return {"status": "skipped", "reason": "no_recipients", "recipient_count": 0}

    attachments = [
        _attachment(archive_dir / SALES_PDF_NAME),
        _attachment(archive_dir / SUPPORT_PDF_NAME),
    ]
    subject, body_text, body_html = _email_content(
        reporting_period=reporting_period,
        generated_at=generated_at,
        timezone=config.timezone,
        summary=summary,
    )
    primary, *bcc = config.recipients
    ok, debug = email_service.send_email(
        db=db,
        to_email=primary,
        bcc_emails=bcc or None,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        attachments=attachments,
        track=False,
        from_name="DotMac Omni Reporting",
    )
    if not ok or (debug and debug.get("refused")):
        error = (debug or {}).get("error") or "SMTP rejected one or more Weekly Reporting recipients."
        return {
            "status": "failed",
            "error": error,
            "recipient_count": len(config.recipients),
        }
    return {
        "status": "sent",
        "recipient_count": len(config.recipients),
        "subject": subject,
    }
