"""Structured filesystem execution logs for Weekly Reporting."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def new_execution_record(*, started_at: datetime, reporting_period: str, period_slug: str) -> dict[str, Any]:
    return {
        "execution_id": uuid.uuid4().hex,
        "execution_start_time": started_at.astimezone(UTC).isoformat(),
        "execution_end_time": None,
        "reporting_period": reporting_period,
        "reporting_period_slug": period_slug,
        "status": "started",
        "active_inboxes_processed": 0,
        "conversations_analysed": 0,
        "sales_conversations_identified": 0,
        "support_conversations_identified": 0,
        "generated_report_locations": [],
        "email_delivery_status": "not_attempted",
        "recipient_count": 0,
        "warnings": [],
        "errors": [],
    }


def write_execution_log(record: dict[str, Any], *, reports_root: Path, ended_at: datetime) -> Path:
    record["execution_end_time"] = ended_at.astimezone(UTC).isoformat()
    log_dir = reports_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    started = str(record["execution_start_time"]).replace(":", "").replace("-", "")
    started = started.replace("+0000", "Z").replace("+00:00", "Z")
    log_path = log_dir / f"weekly_reporting_{started}_{record['execution_id']}.json"
    temporary_path = log_dir / f".{log_path.name}.tmp"
    temporary_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary_path, log_path)
    return log_path
