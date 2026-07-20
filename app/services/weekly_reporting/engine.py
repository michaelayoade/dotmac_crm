"""Read-only orchestration for the approved weekly Sales and Support reports."""

from __future__ import annotations

import fcntl
import hashlib
import importlib
import json
import os
import shutil
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import text

from app.db import SessionLocal
from app.services.weekly_reporting.configuration import WeeklyReportingConfig, load_configuration
from app.services.weekly_reporting.delivery import (
    SALES_PDF_NAME,
    SUPPORT_PDF_NAME,
    deliver_reports,
)
from app.services.weekly_reporting.execution_log import new_execution_record, write_execution_log
from app.services.weekly_reporting.period import period_details

REPORTS_ROOT = Path("reports")
SALES_MARKDOWN_NAME = "Weekly_Sales_Inbound_Experience_Report.md"
SUPPORT_MARKDOWN_NAME = "Weekly_Support_Inbound_Experience_Report.md"
ARCHIVE_METADATA_NAME = "Weekly_Reporting_Execution_Summary.json"
DELIVERY_MARKER_NAME = "Weekly_Reporting_Email_Delivery.json"
REQUIRED_REPORT_NAMES = (
    SALES_MARKDOWN_NAME,
    SALES_PDF_NAME,
    SUPPORT_MARKDOWN_NAME,
    SUPPORT_PDF_NAME,
)


def _load_validated_generators() -> tuple[ModuleType, ModuleType]:
    project_root = Path(__file__).resolve().parents[3]
    scripts_dir = project_root / "scripts"
    scripts_path = str(scripts_dir)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    sales = importlib.import_module("weekly_sales_inbound_report")
    support = importlib.import_module("weekly_support_inbound_report")
    return sales, support


@contextmanager
def _execution_lock(reports_root: Path) -> Iterator[None]:
    lock_dir = reports_root / "logs"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".weekly_reporting.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another Weekly Reporting execution is already running.") from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_config_read_only() -> WeeklyReportingConfig:
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        return load_configuration(db)
    finally:
        db.rollback()
        db.close()


def _validate_generator_results(sales_result: dict[str, Any], support_result: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    sales_validation = sales_result.get("validation") or {}
    support_validation = support_result.get("validation") or {}
    conversations = int(sales_result.get("total_conversations_reviewed") or 0)
    support_conversations_reviewed = int(support_result.get("total_inbound_conversations_reviewed") or 0)
    sales_count = int(sales_result.get("total_sales_conversations") or 0)
    support_count = int(support_result.get("total_support_conversations_reviewed") or 0)
    active_inboxes = int(sales_result.get("active_inboxes_reviewed") or 0)

    if sales_result.get("reporting_period") != support_result.get("reporting_period"):
        errors.append("Sales and Support reporting periods do not match.")
    if conversations != support_conversations_reviewed:
        errors.append("Sales and Support inbound conversation totals do not match.")
    if active_inboxes != int(support_result.get("active_inboxes_reviewed") or 0):
        errors.append("Sales and Support active inbox totals do not match.")
    for key in ("intent_total", "outcome_total", "sentiment_total", "agent_total"):
        if int(sales_validation.get(key, -1)) != sales_count:
            errors.append(f"Sales {key.replace('_', ' ')} does not reconcile.")
    for key in ("complaint_total", "sentiment_total", "resolution_total", "agent_total", "happiness_total"):
        if int(support_validation.get(key, -1)) != support_count:
            errors.append(f"Support {key.replace('_', ' ')} does not reconcile.")
    if int(sales_validation.get("reviewed", -1)) != conversations:
        errors.append("Sales reviewed total does not reconcile.")
    if int(support_validation.get("inbound_reviewed", -1)) != conversations:
        errors.append("Support reviewed total does not reconcile.")
    if int(sales_validation.get("active_inboxes", -1)) != active_inboxes:
        errors.append("Sales active inbox coverage does not reconcile.")
    if int(support_validation.get("active_inboxes", -1)) != active_inboxes:
        errors.append("Support active inbox coverage does not reconcile.")
    if errors:
        raise ValueError("Weekly Reporting cross-report validation failed:\n- " + "\n- ".join(errors))

    warnings = list(dict.fromkeys([*(sales_result.get("warnings") or []), *(support_result.get("warnings") or [])]))
    return {
        "reporting_period": sales_result["reporting_period"],
        "conversations_analysed": conversations,
        "sales_conversations": sales_count,
        "support_conversations": support_count,
        "active_inboxes": active_inboxes,
        "warnings": warnings,
        "sales_validation": sales_validation,
        "support_validation": support_validation,
    }


def _validate_archive(archive_dir: Path) -> None:
    missing = [
        name
        for name in REQUIRED_REPORT_NAMES
        if not (archive_dir / name).is_file() or (archive_dir / name).stat().st_size <= 0
    ]
    if missing:
        raise ValueError(
            "Existing Weekly Reporting archive is incomplete and will not be overwritten: " + ", ".join(missing)
        )


def _load_archive_summary(archive_dir: Path) -> dict[str, Any]:
    _validate_archive(archive_dir)
    metadata_path = archive_dir / ARCHIVE_METADATA_NAME
    if not metadata_path.is_file():
        raise ValueError("Existing Weekly Reporting archive has no execution summary and will not be overwritten.")
    value = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Existing Weekly Reporting execution summary is invalid.")
    return value


def _generate_archive(
    *,
    sales: ModuleType,
    support: ModuleType,
    as_of: datetime,
    reports_root: Path,
    archive_dir: Path,
) -> dict[str, Any]:
    staging_parent = reports_root / "weekly" / ".staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging_dir = staging_parent / f"{archive_dir.name}-{uuid.uuid4().hex}"
    staging_dir.mkdir(parents=False, exist_ok=False)
    try:
        sales_result = sales.generate(staging_dir, now=as_of)
        support_result = support.generate(staging_dir, now=as_of)
        summary = _validate_generator_results(sales_result, support_result)
        _validate_archive(staging_dir)
        generated_at = datetime.now(UTC)
        summary["generated_at"] = generated_at.isoformat()
        summary["report_files"] = list(REQUIRED_REPORT_NAMES)
        (staging_dir / ARCHIVE_METADATA_NAME).write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        archive_dir.parent.mkdir(parents=True, exist_ok=True)
        if archive_dir.exists():
            raise FileExistsError(f"Weekly Reporting archive already exists: {archive_dir}")
        os.replace(staging_dir, archive_dir)
        return summary
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)


def _delivery_marker_path(archive_dir: Path) -> Path:
    return archive_dir / DELIVERY_MARKER_NAME


def _load_delivery_marker(archive_dir: Path) -> dict[str, Any] | None:
    marker_path = _delivery_marker_path(archive_dir)
    if not marker_path.is_file():
        return None
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unknown"}
    if not isinstance(marker, dict):
        return {"status": "unknown"}
    if "status" not in marker and marker.get("delivered_at"):
        marker["status"] = "sent"
    return marker


def _reserve_delivery(
    archive_dir: Path,
    *,
    attempted_at: datetime,
    recipient_count: int,
    recipients: tuple[str, ...],
) -> None:
    marker_path = _delivery_marker_path(archive_dir)
    recipients_digest = hashlib.sha256("\n".join(sorted(recipients)).encode()).hexdigest()
    payload = {
        "status": "sending",
        "attempted_at": attempted_at.astimezone(UTC).isoformat(),
        "recipient_count": recipient_count,
        "recipients_digest": recipients_digest,
    }
    with marker_path.open("x", encoding="utf-8") as marker_file:
        marker_file.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _finalize_delivery_marker(
    archive_dir: Path,
    *,
    status: str,
    completed_at: datetime,
    recipient_count: int,
    recipients: tuple[str, ...],
    subject: str | None = None,
    error: str | None = None,
) -> None:
    recipients_digest = hashlib.sha256("\n".join(sorted(recipients)).encode()).hexdigest()
    marker_path = _delivery_marker_path(archive_dir)
    temporary_path = archive_dir / f".{DELIVERY_MARKER_NAME}.tmp"
    payload = {
        "status": status,
        "completed_at": completed_at.astimezone(UTC).isoformat(),
        "recipient_count": recipient_count,
        "recipients_digest": recipients_digest,
    }
    if status == "sent":
        payload["delivered_at"] = completed_at.astimezone(UTC).isoformat()
    if subject:
        payload["subject"] = subject
    if error:
        payload["error"] = error
    temporary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary_path, marker_path)


def _deliver_read_only(
    *,
    config: WeeklyReportingConfig,
    archive_dir: Path,
    summary: dict[str, Any],
    generated_at: datetime,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        return deliver_reports(
            db,
            config=config,
            archive_dir=archive_dir,
            reporting_period=str(summary["reporting_period"]),
            generated_at=generated_at,
            summary=summary,
        )
    finally:
        db.rollback()
        db.close()


def run_weekly_reporting(
    *,
    now_utc: datetime | None = None,
    reports_root: Path | None = None,
) -> dict[str, Any]:
    """Run the parameterless production workflow; optional arguments support deterministic tests."""
    started_at = now_utc or datetime.now(UTC)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    root = reports_root or REPORTS_ROOT
    reporting_period, period_slug = period_details(started_at)
    record = new_execution_record(
        started_at=started_at,
        reporting_period=reporting_period,
        period_slug=period_slug,
    )
    log_path: Path | None = None

    try:
        with _execution_lock(root):
            sales, support = _load_validated_generators()
            config = _load_config_read_only()
            record["recipient_count"] = len(config.recipients)
            if not config.enabled:
                record["status"] = "skipped"
                record["email_delivery_status"] = "skipped_disabled"
                record["warnings"].append("Weekly Reporting is disabled in Notification Settings.")
                return record

            archive_dir = root / "weekly" / period_slug
            if archive_dir.exists():
                summary = _load_archive_summary(archive_dir)
                record["warnings"].append("A complete archive already existed and was reused without overwrite.")
            else:
                summary = _generate_archive(
                    sales=sales,
                    support=support,
                    as_of=started_at,
                    reports_root=root,
                    archive_dir=archive_dir,
                )

            record["active_inboxes_processed"] = int(summary["active_inboxes"])
            record["conversations_analysed"] = int(summary["conversations_analysed"])
            record["sales_conversations_identified"] = int(summary["sales_conversations"])
            record["support_conversations_identified"] = int(summary["support_conversations"])
            record["generated_report_locations"] = [
                str(archive_dir / SALES_PDF_NAME),
                str(archive_dir / SUPPORT_PDF_NAME),
            ]
            record["warnings"].extend(summary.get("warnings") or [])

            delivery_marker = _load_delivery_marker(archive_dir)
            if delivery_marker:
                marker_status = str(delivery_marker.get("status") or "unknown")
                record["status"] = "completed" if marker_status == "sent" else "completed_with_warnings"
                record["email_delivery_status"] = (
                    "already_delivered" if marker_status == "sent" else "previous_delivery_state_unknown"
                )
                if marker_status == "sent":
                    record["warnings"].append(
                        "Email delivery was already completed for this archive; no duplicate was sent."
                    )
                else:
                    record["warnings"].append(
                        "A previous email delivery attempt has an incomplete or failed state; automatic resend was "
                        "suppressed to prevent duplicate delivery."
                    )
                return record
            if not config.recipients:
                record["status"] = "completed_with_warnings"
                record["email_delivery_status"] = "skipped_no_recipients"
                record["warnings"].append("No Weekly Reporting recipients are configured; email was not sent.")
                return record

            generated_at = datetime.fromisoformat(str(summary["generated_at"]))
            _reserve_delivery(
                archive_dir,
                attempted_at=datetime.now(UTC),
                recipient_count=len(config.recipients),
                recipients=config.recipients,
            )
            delivery = _deliver_read_only(
                config=config,
                archive_dir=archive_dir,
                summary=summary,
                generated_at=generated_at,
            )
            record["email_delivery_status"] = delivery["status"]
            if delivery["status"] != "sent":
                delivery_error = str(delivery.get("error") or "Weekly Reporting email delivery failed.")
                _finalize_delivery_marker(
                    archive_dir,
                    status="failed",
                    completed_at=datetime.now(UTC),
                    recipient_count=len(config.recipients),
                    recipients=config.recipients,
                    error=delivery_error,
                )
                raise RuntimeError(delivery_error)
            _finalize_delivery_marker(
                archive_dir,
                status="sent",
                completed_at=datetime.now(UTC),
                recipient_count=int(delivery["recipient_count"]),
                recipients=config.recipients,
                subject=str(delivery["subject"]),
            )
            record["status"] = "completed"
            return record
    except Exception as exc:
        record["status"] = "failed"
        record["errors"].append(str(exc))
        if record["email_delivery_status"] == "not_attempted":
            record["email_delivery_status"] = "not_sent_due_to_failure"
        return record
    finally:
        record["warnings"] = list(dict.fromkeys(record["warnings"]))
        log_path = write_execution_log(record, reports_root=root, ended_at=datetime.now(UTC))
        record["execution_log"] = str(log_path)
