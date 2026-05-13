"""Admin reports web routes."""

import csv
import io
import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypedDict
from urllib.parse import quote, urlencode
from uuid import UUID
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.csrf import get_csrf_token
from app.db import get_db
from app.models.crm.conversation import Conversation
from app.models.dispatch import TechnicianProfile
from app.models.person import Person, PersonChannel
from app.models.projects import ProjectTask, ProjectTaskAssignee, TaskStatus
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.tickets import Ticket, TicketComment
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.services import operations_sla_reports as operations_sla_reports_service
from app.services.auth_dependencies import require_any_permission
from app.services.crm import reports as crm_reports_service
from app.services.crm import team as crm_team_service
from app.services.quarterly_reports import build_quarterly_report
from app.tasks.subscribers import sync_subscribers_from_splynx
from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats
from app.web.templates import Jinja2Templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["admin-reports"])
templates = Jinja2Templates(directory="templates")


class _ProjectTaskPersonAccumulator(TypedDict):
    id: str
    name: str
    assigned_tasks: int
    completed_tasks: int
    open_tasks: int
    blocked_tasks: int
    overdue_tasks: int
    on_time_tasks: int
    cycle_hours_total: float
    cycle_hours_count: int
    effort_accuracy_total: float
    effort_accuracy_count: int


_NCC_EXPORT_FILENAME = "NCC REPORTS (DOTMAC).xlsx"
_NCC_COLUMNS = [
    "MSISDN",
    "First Name",
    "Last Name",
    "Email",
    "Age",
    "Gender",
    "created date time",
    "Subject",
    "Category",
    "category code (auto)",
    "sub category code",
    "Description (auto)",
    "Ticket ID",
    "Complaint type",
    "Status",
    "Resolved date",
    "Resolution Note",
    "User Note",
    "user notes datetime",
    "Language",
    "Ticket source",
    "alt phone number",
    "created by",
    "State",
    "LGA",
    "Town",
]

_ONLINE_LAST_24H_TICKET_STATUS_OPTIONS = [
    {"value": "all", "label": "All ticket states"},
    {"value": "no_ticket", "label": "No tickets"},
    {"value": "new", "label": "New"},
    {"value": "open", "label": "Open"},
    {"value": "pending", "label": "Pending"},
    {"value": "waiting_on_customer", "label": "Waiting On Customer"},
    {"value": "lastmile_rerun", "label": "Lastmile Rerun"},
    {"value": "site_under_construction", "label": "Site Under Construction"},
    {"value": "on_hold", "label": "On Hold"},
    {"value": "closed", "label": "Closed"},
    {"value": "canceled", "label": "Canceled"},
    {"value": "merged", "label": "Merged"},
]

_ONLINE_LAST_24H_NOTIFICATION_STATE_OPTIONS = [
    {"value": "all", "label": "All notifications"},
    {"value": "notified", "label": "Notified"},
    {"value": "unnotified", "label": "Not Notified"},
]

_ONLINE_LAST_24H_ACTIVITY_SEGMENT_OPTIONS = [
    {"value": "last_24h", "label": "Last online within 24h"},
    {"value": "active_last24_not_online", "label": "Active, last online within 24h, not currently online"},
]
_ONLINE_LAST_24H_WHATSAPP_TARGET_NAMES = {"dotmac fiber helpdesk"}
_ONLINE_LAST_24H_EMAIL_TARGET_NAMES = {"sales mail", "noc mail", "support mail"}
_ONLINE_LAST_24H_ROWS_TTL_SECONDS = 120.0
_ONLINE_LAST_24H_ROWS_CACHE: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = {}
_ONLINE_LAST_24H_ROWS_CACHE_LOCK = threading.Lock()


def _clone_report_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _online_last_24h_cache_key(
    *,
    status: str,
    region: str,
    search: str,
    ticket_status: str,
    notification_state: str,
    activity_segment: str,
    subscriber_ids: list[Any] | None,
) -> tuple[Any, ...]:
    subscriber_scope = None if subscriber_ids is None else tuple(sorted(str(value) for value in subscriber_ids))
    return (
        status,
        region,
        search.strip().lower(),
        ticket_status,
        notification_state,
        activity_segment,
        subscriber_scope,
    )


def _online_last_24h_cached_rows(
    cache_key: tuple[Any, ...],
    builder: Callable[[], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], bool]:
    now = time.monotonic()
    with _ONLINE_LAST_24H_ROWS_CACHE_LOCK:
        cached = _ONLINE_LAST_24H_ROWS_CACHE.get(cache_key)
        if cached is not None:
            expires_at, rows = cached
            if expires_at > now:
                return _clone_report_rows(rows), True
            _ONLINE_LAST_24H_ROWS_CACHE.pop(cache_key, None)

    rows = builder()
    safe_rows = _clone_report_rows(rows)
    with _ONLINE_LAST_24H_ROWS_CACHE_LOCK:
        _ONLINE_LAST_24H_ROWS_CACHE[cache_key] = (
            time.monotonic() + _ONLINE_LAST_24H_ROWS_TTL_SECONDS,
            safe_rows,
        )
    return _clone_report_rows(safe_rows), False


def _online_last_24h_allowed_target_ids(db: Session, channel: str) -> set[str]:
    from app.services.crm.web_campaigns import outreach_channel_target_options

    selected_channel = (channel or "").strip().lower()
    allowed_names = (
        _ONLINE_LAST_24H_WHATSAPP_TARGET_NAMES
        if selected_channel == "whatsapp"
        else _ONLINE_LAST_24H_EMAIL_TARGET_NAMES
        if selected_channel == "email"
        else set()
    )
    if not allowed_names:
        return set()
    options = outreach_channel_target_options(db).get(selected_channel, [])
    return {
        str(option.get("target_id") or "").strip()
        for option in options
        if str(option.get("name") or "").strip().lower() in allowed_names
    }


def _ticket_status_kpi_label(status_value: str) -> str:
    if not status_value:
        return "No Ticket"
    return status_value.replace("_", " ").title()


def _online_last_24h_ticket_status_cards(rows: list[dict]) -> list[dict[str, int | str]]:
    tracked_statuses = ["open", "closed", "canceled", "pending"]
    ticket_status_counts: dict[str, int] = {}

    for row in rows:
        status_value = str(row.get("ticket_status") or "").strip().lower()
        if not status_value:
            continue
        ticket_status_counts[status_value] = ticket_status_counts.get(status_value, 0) + 1

    return [
        {
            "label": _ticket_status_kpi_label(status_value),
            "value": ticket_status_counts.get(status_value, 0),
        }
        for status_value in tracked_statuses
    ]


def _online_last_24h_base_station_options(rows: list[dict]) -> list[str]:
    return sorted(
        {str(row.get("base_station") or "").strip() for row in rows if str(row.get("base_station") or "").strip()},
        key=str.lower,
    )


def _normalize_online_last_24h_base_station_values(base_station: list[str] | str | object) -> list[str]:
    if isinstance(base_station, list):
        values = base_station
    elif isinstance(base_station, str):
        values = [base_station]
    else:
        values = []
    normalized: list[str] = []
    for value in values:
        for part in str(value).split(","):
            candidate = part.strip()
            if candidate and candidate not in normalized:
                normalized.append(candidate)
    return normalized


def _filter_online_last_24h_base_stations(rows: list[dict], selected_base_stations: list[str]) -> list[dict]:
    selected = {value.strip().lower() for value in selected_base_stations if value and value.strip()}
    if not selected:
        return rows
    return [row for row in rows if str(row.get("base_station") or "").strip().lower() in selected]


def _enrich_online_last_24h_campaign_status(rows: list[dict], db: Session) -> list[dict]:
    """Attach latest online-last-24h campaign recipient status to report rows."""
    from app.models.crm.campaign import Campaign, CampaignRecipient
    from app.services.crm.web_campaigns import OUTREACH_SOURCE_ONLINE_LAST_24H

    if db is None:
        return rows

    subscriber_ids: list[UUID] = []
    for row in rows:
        try:
            subscriber_ids.append(UUID(str(row.get("subscriber_id") or "").strip()))
        except (TypeError, ValueError):
            continue
    if not subscriber_ids:
        return rows

    subscriber_person_rows = db.execute(
        select(Subscriber.id, Subscriber.person_id).where(Subscriber.id.in_(subscriber_ids))
    ).all()
    person_to_subscribers: dict[str, list[str]] = {}
    for subscriber_id, person_id in subscriber_person_rows:
        if person_id:
            person_to_subscribers.setdefault(str(person_id), []).append(str(subscriber_id))
    if not person_to_subscribers:
        return rows

    recipient_rows = (
        db.query(CampaignRecipient, Campaign)
        .join(Campaign, Campaign.id == CampaignRecipient.campaign_id)
        .filter(CampaignRecipient.person_id.in_([UUID(person_id) for person_id in person_to_subscribers]))
        .order_by(func.coalesce(CampaignRecipient.sent_at, CampaignRecipient.created_at).desc())
        .all()
    )
    latest_by_subscriber: dict[str, CampaignRecipient] = {}
    for recipient, campaign in recipient_rows:
        metadata = campaign.metadata_ or {}
        if not isinstance(metadata, dict) or metadata.get("source_report") != OUTREACH_SOURCE_ONLINE_LAST_24H:
            continue
        for subscriber_id in person_to_subscribers.get(str(recipient.person_id), []):
            latest_by_subscriber.setdefault(subscriber_id, recipient)

    for row in rows:
        recipient = latest_by_subscriber.get(str(row.get("subscriber_id") or ""))
        if not recipient:
            row.setdefault("latest_notification_sent_status", "")
            row.setdefault("latest_notification_sent_for", "")
            continue
        status = recipient.status.value if hasattr(recipient.status, "value") else str(recipient.status or "")
        sent_at = recipient.sent_at or recipient.delivered_at or recipient.created_at
        row["latest_notification_sent_status"] = status
        row["latest_notification_sent_for"] = sent_at.strftime("%Y-%m-%d %H:%M") if sent_at else ""
    return rows


def _filter_online_last_24h_notification_state(rows: list[dict], notification_state: str) -> list[dict]:
    normalized = (notification_state or "all").strip().lower()
    if normalized == "notified":
        return [row for row in rows if str(row.get("notification_state") or "").strip().lower() == "notified"]
    if normalized == "unnotified":
        return [row for row in rows if str(row.get("notification_state") or "").strip().lower() == "unnotified"]
    return rows


def _sort_online_last_24h_rows(rows: list[dict]) -> list[dict]:
    """Sort report rows by last seen, newest first."""
    return sorted(rows, key=lambda row: str(row.get("last_seen_at_iso") or row.get("last_seen_at") or ""), reverse=True)


def _normalize_segment_filters(segments: list[str] | str | None, segment: str | None) -> list[str]:
    """Normalize repeated/comma-separated segment query values."""
    raw_values: list[str] = []
    if isinstance(segments, list):
        raw_values.extend(segments)
    elif isinstance(segments, str):
        raw_values.append(segments)
    if segment:
        raw_values.append(segment)

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        for part in str(raw_value).split(","):
            candidate = part.strip().lower().replace(" ", "_")
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
    return normalized


def _segment_labels(selected_segments: list[str]) -> set[str]:
    mapping = {
        "overdue": "Overdue",
        "suspended": "Suspended",
        "due_soon": "Due Soon",
        "churned": "Churned",
        "pending": "Pending",
    }
    return {mapping[key] for key in selected_segments if key in mapping}


def _parse_date_range(
    days: int | None,
    start_date: str | None,
    end_date: str | None,
) -> tuple[datetime, datetime]:
    """Parse date range from days or custom dates."""
    now = datetime.now(UTC)
    end_dt = now

    if start_date and end_date:
        try:
            start_dt = datetime.fromisoformat(start_date).replace(tzinfo=UTC)
            end_dt = datetime.fromisoformat(end_date).replace(tzinfo=UTC)
            # Ensure end_date is end of day
            end_dt = end_dt.replace(hour=23, minute=59, second=59)
            return start_dt, end_dt
        except ValueError:
            pass

    # Fall back to days
    days = days or 30
    start_dt = now - timedelta(days=days)
    return start_dt, end_dt


def _csv_response(data: list[dict], filename: str) -> StreamingResponse:
    """Create a CSV streaming response."""
    if not data:
        output = io.StringIO()
        output.write("No data available\n")
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=data[0].keys())
    writer.writeheader()
    writer.writerows(data)
    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _excel_column_letter(index: int) -> str:
    result = ""
    current = index
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _excel_serial_from_display_timestamp(value: str) -> float | None:
    cleaned = " ".join((value or "").strip().split())
    if not cleaned:
        return None
    try:
        timestamp = datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=UTC)
    except ValueError:
        return None
    excel_epoch = datetime(1899, 12, 30, tzinfo=UTC)
    delta = timestamp - excel_epoch
    return delta.days + (delta.seconds / 86400)


def _xlsx_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _ncc_export_column_widths(records: list[dict[str, str]], columns: list[str]) -> list[float]:
    fixed_widths = {
        "MSISDN": 18,
        "First Name": 22,
        "Last Name": 22,
        "Email": 28,
        "Age": 10,
        "Gender": 12,
        "created date time": 22,
        "Subject": 28,
        "Category": 24,
        "category code (auto)": 20,
        "sub category code": 22,
        "Description (auto)": 42,
        "Ticket ID": 16,
        "Complaint type": 24,
        "Status": 18,
        "Resolved date": 22,
        "Resolution Note": 36,
        "User Note": 36,
        "user notes datetime": 22,
        "Language": 14,
        "Ticket source": 18,
        "alt phone number": 20,
        "created by": 24,
        "State": 14,
        "LGA": 14,
        "Town": 18,
    }
    widths: list[float] = []
    for column in columns:
        width = fixed_widths.get(column, max(len(column) + 2, 14))
        if column not in fixed_widths:
            max_value_length = max((len(str(row.get(column) or "")) for row in records), default=0)
            width = min(max(max_value_length + 2, len(column) + 2, 14), 24)
        widths.append(float(width))
    return widths


def _ncc_status_style_id(status_variant: str) -> int:
    mapping = {
        "success": 5,
        "warning": 6,
        "error": 7,
        "info": 8,
    }
    return mapping.get(status_variant, 9)


def _build_ncc_workbook(records: list[dict[str, str]], columns: list[str]) -> bytes:
    long_text_columns = {"Description (auto)", "Resolution Note", "User Note"}
    date_columns = {"created date time", "Resolved date", "user notes datetime"}
    widths = _ncc_export_column_widths(records, columns)
    output = io.BytesIO()

    def cell_xml(ref: str, value: str, style_id: int) -> str:
        return (
            f'<c r="{ref}" s="{style_id}" t="inlineStr"><is><t xml:space="preserve">'
            f"{escape(str(value or ''))}</t></is></c>"
        )

    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        )
        generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        archive.writestr(
            "docProps/core.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{escape(_NCC_EXPORT_FILENAME)}</dc:title>
  <dc:creator>Dotmac CRM</dc:creator>
  <cp:lastModifiedBy>Dotmac CRM</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{generated_at}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{generated_at}</dcterms:modified>
</cp:coreProperties>""",
        )
        archive.writestr(
            "docProps/app.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Dotmac CRM</Application>
</Properties>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="NCC Reports" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/styles.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="1">
    <numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/>
  </numFmts>
  <fonts count="2">
    <font>
      <sz val="11"/>
      <color theme="1"/>
      <name val="Calibri"/>
      <family val="2"/>
    </font>
    <font>
      <b/>
      <sz val="11"/>
      <color rgb="FFFFFFFF"/>
      <name val="Calibri"/>
      <family val="2"/>
    </font>
  </fonts>
  <fills count="7">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF16A34A"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFDCFCE7"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFFEF3C7"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFFEE2E2"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFDBEAFE"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border>
      <left/><right/><top/><bottom/><diagonal/>
    </border>
    <border>
      <left style="thin"><color rgb="FFD1D5DB"/></left>
      <right style="thin"><color rgb="FFD1D5DB"/></right>
      <top style="thin"><color rgb="FFD1D5DB"/></top>
      <bottom style="thin"><color rgb="FFD1D5DB"/></bottom>
      <diagonal/>
    </border>
  </borders>
  <cellStyleXfs count="1">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
  </cellStyleXfs>
  <cellXfs count="10">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="top"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>
    <xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>
    <xf numFmtId="0" fontId="0" fillId="4" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>
    <xf numFmtId="0" fontId="0" fillId="5" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>
    <xf numFmtId="0" fontId="0" fillId="6" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>
  </cellXfs>
  <cellStyles count="1">
    <cellStyle name="Normal" xfId="0" builtinId="0"/>
  </cellStyles>
</styleSheet>""",
        )

        last_column_letter = _excel_column_letter(len(columns))
        last_row_number = len(records) + 1
        cols_xml = "".join(
            f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
            for index, width in enumerate(widths, start=1)
        )
        rows_xml: list[str] = []
        header_cells = [
            cell_xml(f"{_excel_column_letter(index)}1", column, 1) for index, column in enumerate(columns, start=1)
        ]
        rows_xml.append(f'<row r="1" ht="24" customHeight="1">{"".join(header_cells)}</row>')
        for row_number, row in enumerate(records, start=2):
            cells: list[str] = []
            for column_index, column in enumerate(columns, start=1):
                value = " ".join(str(row.get(column) or "").strip().split())
                if not value:
                    continue
                cell_ref = f"{_excel_column_letter(column_index)}{row_number}"
                if column in date_columns:
                    serial_value = _excel_serial_from_display_timestamp(value)
                    if serial_value is not None:
                        cells.append(f'<c r="{cell_ref}" s="4"><v>{serial_value}</v></c>')
                        continue
                if column == "Status":
                    style_id = _ncc_status_style_id(str(row.get("_status_variant") or ""))
                elif column in long_text_columns:
                    style_id = 3
                else:
                    style_id = 2
                cells.append(cell_xml(cell_ref, value, style_id))
            rows_xml.append(f'<row r="{row_number}">{"".join(cells)}</row>')

        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <dimension ref="A1:{last_column_letter}{last_row_number}"/>
  <sheetViews>
    <sheetView workbookViewId="0">
      <pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>
      <selection pane="bottomLeft" activeCell="A2" sqref="A2"/>
    </sheetView>
  </sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <cols>{cols_xml}</cols>
  <sheetData>{"".join(rows_xml)}</sheetData>
  <autoFilter ref="A1:{last_column_letter}{last_row_number}"/>
</worksheet>""",
        )
    return output.getvalue()


def _append_query_flag(url: str, key: str, value: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{quote(key)}={quote(value)}"


def _toast_redirect(url: str, *, message: str, toast_type: str = "success", status_code: int = 303) -> RedirectResponse:
    headers = {
        "HX-Trigger": json.dumps(
            {
                "showToast": {
                    "type": toast_type,
                    "message": message,
                }
            }
        )
    }
    return RedirectResponse(url=url, status_code=status_code, headers=headers)


def _latest_subscriber_sync_at(db: Session) -> datetime | None:
    latest = db.scalar(select(func.max(Subscriber.last_synced_at)))
    if latest is None:
        return None
    if latest.tzinfo is None:
        return latest.replace(tzinfo=UTC)
    return latest.astimezone(UTC)


def _resolve_lifecycle_date_range(
    db: Session,
    days: int | None,
    start_date: str | None,
    end_date: str | None,
) -> tuple[datetime, datetime]:
    """Resolve lifecycle report range, defaulting to inception when days is 0/None."""
    if start_date and end_date:
        return _parse_date_range(days, start_date, end_date)

    if days and days > 0:
        return _parse_date_range(days, start_date, end_date)

    now = datetime.now(UTC)
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    inception = db.scalar(select(func.min(activation_event_at)))
    if inception is None:
        return now - timedelta(days=30), now
    if inception.tzinfo is None:
        inception = inception.replace(tzinfo=UTC)
    else:
        inception = inception.astimezone(UTC)
    return inception, now


def _display_timestamp(value: datetime | None) -> str:
    if value is None:
        return ""
    normalized = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return normalized.strftime("%Y-%m-%d %H:%M:%S UTC")


def _display_enum(value: object | None) -> str:
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    text = str(raw).strip()
    if not text:
        return ""
    return text.replace("_", " ").title()


def _person_name(person: Person | None) -> str:
    if person is None:
        return ""
    full_name = " ".join(part for part in [person.first_name, person.last_name] if part).strip()
    return full_name or (person.display_name or "")


def _calculate_age(date_of_birth, reference_at: datetime | None) -> str:
    if not date_of_birth:
        return "N/A"
    reference_date = (
        (reference_at.astimezone(UTC).date() if reference_at and reference_at.tzinfo else reference_at.date())
        if reference_at
        else datetime.now(UTC).date()
    )
    years = reference_date.year - date_of_birth.year
    if (reference_date.month, reference_date.day) < (date_of_birth.month, date_of_birth.day):
        years -= 1
    return str(max(years, 0))


def _ticket_primary_person(ticket: Ticket) -> Person | None:
    if ticket.customer is not None:
        return ticket.customer
    if ticket.subscriber is not None and ticket.subscriber.person is not None:
        return ticket.subscriber.person
    return None


def _ticket_alt_phone(person: Person | None, channels: list[PersonChannel]) -> str:
    if person is None:
        return ""
    normalized_primary = (person.phone or "").strip()
    for channel in channels:
        address = (channel.address or "").strip()
        if not address or address == normalized_primary:
            continue
        return address
    return ""


def _split_name(value: str) -> tuple[str, str]:
    cleaned = (value or "").strip()
    if not cleaned:
        return "", ""
    parts = cleaned.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _title_case_name(value: str) -> str:
    cleaned = " ".join((value or "").strip().split())
    if not cleaned:
        return ""
    if "@" in cleaned:
        return cleaned
    return cleaned.title()


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _title_case_report_value(value: object) -> str:
    cleaned = _clean_text(value)
    if not cleaned or "@" in cleaned:
        return cleaned.lower() if "@" in cleaned else cleaned
    titled = cleaned.title()
    replacements = {
        "Ap/": "AP/",
        "Ap ": "AP ",
        "Sla": "SLA",
        "Ncc": "NCC",
        "Fct": "FCT",
        "Lga": "LGA",
        "Id": "ID",
        "Lan": "LAN",
        "Wan": "WAN",
        "Wifi": "WiFi",
        "Ip": "IP",
        "Dns": "DNS",
        "Onu": "ONU",
        "Ont": "ONT",
        "Olt": "OLT",
        "Los": "LOS",
        "Cpe": "CPE",
        "Noc": "NOC",
        "Crm": "CRM",
        "Sms": "SMS",
        "Whatsapp": "WhatsApp",
    }
    for source, target in replacements.items():
        titled = titled.replace(source, target)
    return titled


def _normalize_msisdn(value: str | None) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    if cleaned.startswith("+"):
        digits = "".join(char for char in cleaned[1:] if char.isdigit())
        return f"+{digits}" if digits else ""
    digits = "".join(char for char in cleaned if char.isdigit())
    if not digits:
        return ""
    if digits.startswith("234") and len(digits) >= 13:
        return f"+{digits}"
    if digits.startswith("0"):
        return digits
    if len(digits) == 10:
        return f"0{digits}"
    if digits.startswith("234"):
        return f"+{digits}"
    return digits


def _complete_ncc_msisdn_or_empty(value: str | None) -> str:
    normalized = _normalize_msisdn(value)
    if not normalized:
        return ""
    digits = "".join(char for char in normalized if char.isdigit())
    if normalized.startswith("+234"):
        return normalized if len(digits) == 13 else ""
    if normalized.startswith("0"):
        return normalized if len(digits) == 11 else ""
    return normalized if 10 <= len(digits) <= 15 else ""


_NCC_EMPTY_MARKERS = {
    "-",
    "--",
    "---",
    "n/a",
    "na",
    "nil",
    "none",
    "null",
    "unknown",
    "not available",
    "not applicable",
    "not specified",
}


def _ncc_clean_basic_text(value: object) -> str:
    cleaned = _clean_text(value)
    if cleaned.lower() in _NCC_EMPTY_MARKERS:
        return ""
    return cleaned


def _ncc_clean_email(value: object) -> str:
    email = _ncc_clean_basic_text(value).lower()
    if not email or "@" not in email:
        return ""
    local_part, _separator, domain = email.partition("@")
    if not local_part or "." not in domain:
        return ""
    return email


def _ncc_clean_title_text(value: object) -> str:
    cleaned = _ncc_clean_basic_text(value)
    if not cleaned:
        return ""
    return _title_case_report_value(cleaned)


def _ncc_clean_name(value: object) -> str:
    cleaned = _ncc_clean_basic_text(value)
    if not cleaned:
        return ""
    return _title_case_name(cleaned)


def _ncc_clean_long_text(value: object) -> str:
    cleaned = _ncc_clean_basic_text(value)
    if not cleaned:
        return ""
    if cleaned.isupper():
        cleaned = cleaned.lower()
    return cleaned[:1].upper() + cleaned[1:]


def _clean_ncc_record(record: dict[str, str]) -> dict[str, str]:
    cleaned = {key: _ncc_clean_basic_text(value) for key, value in record.items()}

    cleaned["MSISDN"] = _complete_ncc_msisdn_or_empty(cleaned.get("MSISDN"))
    cleaned["alt phone number"] = _complete_ncc_msisdn_or_empty(cleaned.get("alt phone number"))
    cleaned["First Name"] = _ncc_clean_name(cleaned.get("First Name"))
    cleaned["Last Name"] = _ncc_clean_name(cleaned.get("Last Name"))
    cleaned["Email"] = _ncc_clean_email(cleaned.get("Email"))

    for column in (
        "Subject",
        "Category",
        "Complaint type",
        "Status",
        "Ticket source",
        "created by",
        "State",
        "LGA",
        "Town",
        "Gender",
        "Language",
    ):
        cleaned[column] = _ncc_clean_title_text(cleaned.get(column))

    for column in ("Description (auto)", "Resolution Note", "User Note"):
        cleaned[column] = _ncc_clean_long_text(cleaned.get(column))

    return cleaned


def _normalize_person_name_parts(first_name: str, last_name: str) -> tuple[str, str]:
    honorifics = {
        "mr",
        "mr.",
        "mrs",
        "mrs.",
        "miss",
        "ms",
        "ms.",
        "dr",
        "dr.",
        "prof",
        "prof.",
        "chief",
        "chief.",
        "alhaji",
        "alh.",
        "pastor",
        "pastor.",
    }
    normalized_first = (first_name or "").strip()
    normalized_last = (last_name or "").strip()
    if normalized_first.lower() not in honorifics or not normalized_last:
        return normalized_first, normalized_last

    last_parts = normalized_last.split()
    normalized_first = f"{normalized_first} {last_parts[0]}".strip()
    normalized_last = " ".join(last_parts[1:]).strip()
    return normalized_first, normalized_last


def _looks_like_business_name(value: str) -> bool:
    cleaned = (value or "").strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    business_markers = {
        "ltd",
        "limited",
        "enterprise",
        "enterprises",
        "services",
        "service",
        "global",
        "company",
        "ventures",
        "ventues",
        "nigeria",
        "school",
        "bank",
        "hotel",
        "clinic",
        "hospital",
        "church",
        "mosque",
        "foundation",
        "group",
        "logistics",
        "network",
        "networks",
        "technologies",
        "technology",
        "tech",
        "interior",
        "concept",
        "plaza",
        "mart",
        "stores",
        "apartments",
        "estate",
        "hub",
        "resort",
        "resorts",
        "suite",
        "suites",
        "integrated",
        "royal",
        "events",
    }
    words = {
        token
        for token in "".join(char if char.isalnum() or char.isspace() else " " for char in lowered).split()
        if token
    }
    return any(marker in words for marker in business_markers)


def _label_to_name_parts(value: str, *, treat_as_business: bool = False) -> tuple[str, str]:
    cleaned = (value or "").strip()
    if not cleaned:
        return "", ""
    if treat_as_business or _looks_like_business_name(cleaned):
        return cleaned, ""
    return _split_name(cleaned)


def _ticket_name_parts(ticket: Ticket, person: Person | None) -> tuple[str, str]:
    first_name = (person.first_name or "").strip() if person else ""
    last_name = (person.last_name or "").strip() if person else ""
    if first_name or last_name:
        combined_name = " ".join(part for part in [first_name, last_name] if part).strip()
        if _looks_like_business_name(combined_name):
            return _title_case_name(combined_name), ""
        first_name, last_name = _normalize_person_name_parts(first_name, last_name)
        return _title_case_name(first_name), _title_case_name(last_name)

    fallback_values: list[str] = []
    if person and person.display_name:
        fallback_values.append(person.display_name)
    if person and person.email:
        fallback_values.append(person.email)
    if ticket.subscriber and ticket.subscriber.person and ticket.subscriber.person.display_name:
        fallback_values.append(ticket.subscriber.person.display_name)
    if ticket.subscriber and ticket.subscriber.person and ticket.subscriber.person.email:
        fallback_values.append(ticket.subscriber.person.email)
    if ticket.subscriber and ticket.subscriber.display_name:
        fallback_values.append(ticket.subscriber.display_name)
    if ticket.subscriber and ticket.subscriber.organization and ticket.subscriber.organization.name:
        fallback_values.append(ticket.subscriber.organization.name)
    if ticket.subscriber and ticket.subscriber.subscriber_number:
        fallback_values.append(ticket.subscriber.subscriber_number)

    for fallback in fallback_values:
        first_name, last_name = _label_to_name_parts(fallback)
        if first_name or last_name:
            return _title_case_name(first_name), _title_case_name(last_name)
    return "", ""


def _ncc_status_variant(ticket: Ticket) -> str:
    status_value = getattr(ticket.status, "value", ticket.status)
    normalized = str(status_value or "").strip().lower()
    if normalized in {"closed"}:
        return "success"
    if normalized in {"canceled"}:
        return "error"
    if normalized in {"pending", "waiting_on_customer", "lastmile_rerun", "site_under_construction", "on_hold"}:
        return "warning"
    if normalized in {"merged"}:
        return "inactive"
    return "info"


def _normalized_ticket_type_code(ticket_type: str | None) -> str:
    raw = (ticket_type or "").strip()
    if not raw:
        return ""
    cleaned = "".join(char if char.isalnum() else "_" for char in raw.upper())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


def _subcategory_code(ticket_type: str | None) -> str:
    normalized = _normalized_ticket_type_code(ticket_type)
    mapping = {
        "AP_AIR_FIBER_OUTAGE": "OUTAGE_AP_AIR_FIBER",
        "AP_LAN_TROUBLESHOOTING": "TROUBLESHOOTING_AP_LAN",
        "CABINET_DISCONNECTION": "DISCONNECTION_CABINET",
        "CALL_DOWN_SUPPORT": "SUPPORT_CALL_DOWN",
        "CORE_LINK_DISCONNECTION": "DISCONNECTION_CORE_LINK",
        "CUSTOMER_LINK_DISCONNECTION": "DISCONNECTION_CUSTOMER_LINK",
        "CUSTOMER_REALIGNMENT": "REALIGNMENT_CUSTOMER",
        "LAN_TROUBLESHOOTING": "TROUBLESHOOTING_LAN",
        "MULTIPLE_CABINET_DISCONNECTION": "DISCONNECTION_CABINET_MULTIPLE",
        "MULTIPLE_CORE_LINK_DISCONNECTION": "DISCONNECTION_CORE_LINK_MULTIPLE",
        "MULTIPLE_CUSTOMER_LINK_DISCONNECTION": "DISCONNECTION_CUSTOMER_LINK_MULTIPLE",
        "POWER_OPTIMIZATION": "OPTIMIZATION_POWER",
        "ROUTER_TROUBLESHOOTING": "TROUBLESHOOTING_ROUTER",
        "SLOW_BROWSING_INTERMITTENT_CONNECTIVITY": "PERFORMANCE_SLOW_INTERMITTENT",
    }
    return mapping.get(normalized, normalized)


def _normalize_ncc_region(value: str | None) -> str:
    cleaned = (value or "").strip().lower()
    if not cleaned:
        return ""
    normalized = "".join(char if char.isalnum() or char.isspace() else " " for char in cleaned)
    return " ".join(normalized.split())


def _map_ncc_location(ticket_region: str | None) -> tuple[str, str, str]:
    town = (ticket_region or "").strip() or "-"
    normalized = _normalize_ncc_region(ticket_region)
    if not normalized:
        return "-", town, "-"

    area_council_aliases = {
        "AMAC": {
            "wuse",
            "maitama",
            "asokoro",
            "garki",
            "jabi",
            "gudu",
            "apo",
            "durumi",
            "utako",
            "mabushi",
            "gwarimpa",
            "lugbe",
            "lokogoma",
            "life camp",
            "lifecamp",
            "katampe",
            "katampe extension",
            "kado",
            "dakibiyu",
            "wuye",
            "wuye district",
            "games village",
            "guzape",
            "guzape district",
            "galadimawa",
            "kabusa",
            "wumba",
            "wumba district",
            "kyami",
            "karmo",
            "karmo district",
            "jikwoyi",
            "karshi",
            "nyanya",
            "orozo",
            "kurudu",
            "kpeyegyi",
            "dakwo",
            "duboyi",
            "kaura",
            "idu",
            "idu industrial",
            "jahi",
            "jahi district",
            "utako district",
            "garki district",
            "gudu district",
            "jabi district",
            "asokoro district",
            "maitama district",
            "wuse 2",
            "wuse ii",
            "wuse zone 1",
            "wuse zone 2",
            "wuse zone 3",
            "wuse zone 4",
            "wuse zone 5",
            "wuse zone 6",
            "wuse zone 7",
        },
        "Bwari": {
            "kubwa",
            "dutse",
            "dutse alhaji",
            "bwari",
            "dei dei",
            "mpape",
            "dawaki",
            "ushafa",
            "byazhin",
        },
        "Gwagwalada": {
            "gwagwalada",
            "zuba",
            "paiko",
            "tunga maje",
            "ibwa",
        },
        "Kuje": {
            "kuje",
            "chukuku",
            "piyanko",
            "rubochi",
        },
        "Abaji": {
            "abaji",
            "yaba",
        },
        "Kwali": {
            "kwali",
            "sheda",
            "pai",
            "yangoji",
            "dafa",
        },
    }

    for lga, aliases in area_council_aliases.items():
        if normalized in aliases:
            return lga, town, "FCT"

    for lga, aliases in area_council_aliases.items():
        for alias in aliases:
            if alias in normalized or normalized in alias:
                return lga, town, "FCT"

    return "-", town, "-"


def _ticket_notes(ticket: Ticket) -> tuple[str, str, str]:
    latest_public: TicketComment | None = None
    latest_internal: TicketComment | None = None

    for comment in sorted(ticket.comments or [], key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC)):
        if comment.is_internal:
            latest_internal = comment
        else:
            latest_public = comment

    resolution_note = (latest_internal.body or "").strip() if latest_internal else ""
    user_note = (latest_public.body or "").strip() if latest_public else ""
    user_note_dt = _display_timestamp(latest_public.created_at) if latest_public else ""
    return resolution_note, user_note, user_note_dt


def _default_ncc_date_values() -> tuple[str, str]:
    end_date = datetime.now(UTC).date()
    start_date = end_date - timedelta(days=7)
    return start_date.isoformat(), end_date.isoformat()


def _parse_ncc_window(start_date: str | None, end_date: str | None) -> tuple[datetime, datetime, str, str]:
    default_start, default_end = _default_ncc_date_values()
    start_value = (start_date or default_start).strip() or default_start
    end_value = (end_date or default_end).strip() or default_end

    try:
        start_dt = datetime.fromisoformat(start_value).replace(tzinfo=UTC)
    except ValueError:
        start_value = default_start
        start_dt = datetime.fromisoformat(start_value).replace(tzinfo=UTC)

    try:
        end_dt = datetime.fromisoformat(end_value).replace(tzinfo=UTC)
    except ValueError:
        end_value = default_end
        end_dt = datetime.fromisoformat(end_value).replace(tzinfo=UTC)

    end_dt = end_dt.replace(hour=23, minute=59, second=59)
    if end_dt < start_dt:
        end_value = start_value
        end_dt = start_dt.replace(hour=23, minute=59, second=59)

    return start_dt, end_dt, start_value, end_value


def _build_ncc_records(db: Session, start_dt: datetime, end_dt: datetime) -> list[dict[str, str]]:
    tickets = (
        db.scalars(
            select(Ticket)
            .options(
                joinedload(Ticket.customer),
                joinedload(Ticket.created_by),
                joinedload(Ticket.subscriber).joinedload(Subscriber.person),
                joinedload(Ticket.subscriber).joinedload(Subscriber.organization),
                joinedload(Ticket.comments).joinedload(TicketComment.author),
            )
            .where(Ticket.created_at >= start_dt, Ticket.created_at <= end_dt)
            .order_by(Ticket.created_at.asc())
        )
        .unique()
        .all()
    )

    ticket_ids = [ticket.id for ticket in tickets]
    conversation_subjects: dict[UUID, str] = {}
    if ticket_ids:
        conversations = db.scalars(
            select(Conversation).where(Conversation.ticket_id.in_(ticket_ids)).order_by(Conversation.created_at.desc())
        ).all()
        for conversation in conversations:
            if conversation.ticket_id and conversation.subject and conversation.ticket_id not in conversation_subjects:
                conversation_subjects[conversation.ticket_id] = conversation.subject.strip()

    people: list[Person] = []
    for ticket in tickets:
        person = _ticket_primary_person(ticket)
        if person is not None:
            people.append(person)

    person_ids = {person.id for person in people}
    channels_by_person: dict[UUID, list[PersonChannel]] = {}
    if person_ids:
        person_channels = db.scalars(
            select(PersonChannel)
            .where(PersonChannel.person_id.in_(person_ids))
            .order_by(PersonChannel.created_at.asc())
        ).all()
        for channel in person_channels:
            channels_by_person.setdefault(channel.person_id, []).append(channel)

    records: list[dict[str, str]] = []
    for ticket in tickets:
        status_value = str(getattr(ticket.status, "value", ticket.status) or "").strip().lower()
        ticket_type = _clean_text(ticket.ticket_type)
        if status_value == "canceled":
            continue
        if "core link disconnection" in ticket_type.lower():
            continue

        person = _ticket_primary_person(ticket)
        first_name, last_name = _ticket_name_parts(ticket, person)
        if not first_name and not last_name:
            continue
        person_channels = channels_by_person.get(person.id, []) if person is not None else []
        resolution_note, user_note, user_note_dt = _ticket_notes(ticket)
        lga, town, state = _map_ncc_location(ticket.region)

        record = _clean_ncc_record(
            {
                "MSISDN": _complete_ncc_msisdn_or_empty(person.phone if person else None),
                "First Name": first_name,
                "Last Name": last_name,
                "Email": _clean_text(person.email).lower() if person and person.email else "",
                "Age": _calculate_age(person.date_of_birth if person else None, ticket.created_at),
                "Gender": _display_enum(person.gender)
                if person and getattr(person.gender, "value", "unknown") != "unknown"
                else "N/A",
                "created date time": _display_timestamp(ticket.created_at),
                "Subject": _title_case_report_value(conversation_subjects.get(ticket.id, ""))
                or _title_case_report_value(ticket.title),
                "Category": _title_case_report_value(ticket_type),
                "category code (auto)": "",
                "sub category code": "",
                "Description (auto)": _clean_text(ticket.description),
                "Ticket ID": ticket.number or str(ticket.id),
                "Complaint type": _title_case_report_value(ticket_type),
                "Status": _display_enum(ticket.status),
                "Resolved date": _display_timestamp(ticket.resolved_at),
                "Resolution Note": _clean_text(resolution_note),
                "User Note": _clean_text(user_note),
                "user notes datetime": user_note_dt,
                "Language": "English",
                "Ticket source": _title_case_report_value(_display_enum(ticket.channel)),
                "alt phone number": _complete_ncc_msisdn_or_empty(_ticket_alt_phone(person, person_channels)),
                "created by": _title_case_report_value(_person_name(ticket.created_by)),
                "State": state,
                "LGA": lga,
                "Town": _title_case_report_value(town) if town != "-" else town,
                "_ticket_url": f"/admin/support/tickets/{ticket.number or ticket.id}",
                "_status_variant": _ncc_status_variant(ticket),
            }
        )
        if not record["First Name"] and not record["Last Name"]:
            continue
        records.append(record)
    return records


def _ncc_export_rows(records: list[dict[str, str]]) -> list[dict[str, str]]:
    export_rows: list[dict[str, str]] = []
    for record in records:
        export_rows.append({key: value for key, value in record.items() if not key.startswith("_")})
    return export_rows


@router.get("/operations")
def operations_report_alias():
    return RedirectResponse(url="/admin/operations/work-orders", status_code=302)


@router.get(
    "/quarterly",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission("reports:operations", "reports"))],
)
def quarterly_report(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    load_error = ""
    report: dict[str, object] = {
        "sources": {
            "customer_workbook": "Dotmac Customer internet usage.xlsx",
            "plan_workbook": "Internet plan usage.xlsx",
        }
    }
    try:
        report = build_quarterly_report()
    except FileNotFoundError as exc:
        logger.warning("quarterly_report_missing_source path=%s", exc.filename)
        load_error = "source_missing"

    return templates.TemplateResponse(
        "admin/reports/quarterly_report.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "quarterly-report",
            "active_menu": "reports",
            "report": report,
            "load_error": load_error,
        },
    )


@router.get(
    "/ncc",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission("reports:operations", "reports"))],
)
def ncc_reports_page(
    request: Request,
    db: Session = Depends(get_db),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    user = get_current_user(request)
    start_dt, end_dt, start_value, end_value = _parse_ncc_window(start_date, end_date)
    records = _build_ncc_records(db, start_dt, end_dt)

    return templates.TemplateResponse(
        "admin/reports/ncc_reports.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "ncc-reports",
            "active_menu": "reports",
            "columns": _NCC_COLUMNS,
            "records": records,
            "window_start": start_value,
            "window_end": end_value,
        },
    )


@router.get(
    "/ncc/export",
    response_class=StreamingResponse,
    dependencies=[Depends(require_any_permission("reports:operations", "reports"))],
)
def ncc_reports_export(
    db: Session = Depends(get_db),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    start_dt, end_dt, _start_value, _end_value = _parse_ncc_window(start_date, end_date)
    records = _ncc_export_rows(_build_ncc_records(db, start_dt, end_dt))
    workbook = _build_ncc_workbook(records, _NCC_COLUMNS)
    return _xlsx_response(workbook, _NCC_EXPORT_FILENAME)


@router.get("/operations-sla-violations", response_class=HTMLResponse)
def operations_sla_violations_report(
    request: Request,
    db: Session = Depends(get_db),
    data_type: str = Query("ticket"),
    region: str | None = Query(None),
    days: int = Query(30, ge=1, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    user = get_current_user(request)
    _valid_types = {"ticket", "project", "project_task"}
    selected_type: Literal["ticket", "project", "project_task"] = (
        data_type if data_type in _valid_types else "ticket"  # type: ignore[assignment]
    )
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    report = operations_sla_reports_service.operations_sla_violations_report
    region_options = report.region_options(db, selected_type)
    selected_region = region if region in region_options else None

    summary = report.summary(
        db,
        entity_type=selected_type,
        region=selected_region,
        start_at=start_dt,
        end_at=end_dt,
        open_only=True,
    )
    region_chart = report.by_region(
        db,
        entity_type=selected_type,
        region=selected_region,
        start_at=start_dt,
        end_at=end_dt,
        open_only=True,
    )
    trend_chart = report.trend_daily(
        db,
        entity_type=selected_type,
        region=selected_region,
        start_at=start_dt,
        end_at=end_dt,
        open_only=True,
    )
    records = report.list_records(
        db,
        entity_type=selected_type,
        region=selected_region,
        start_at=start_dt,
        end_at=end_dt,
        open_only=True,
    )

    data_type_options = [
        {"value": "ticket", "label": "Tickets"},
        {"value": "project", "label": "Projects"},
        {"value": "project_task", "label": "Project Tasks"},
    ]

    return templates.TemplateResponse(
        "admin/reports/operations_sla_violations.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "active_menu": "reports",
            "active_page": "operations-sla-violations",
            "sidebar_stats": get_sidebar_stats(db),
            "data_type_options": data_type_options,
            "selected_data_type": selected_type,
            "region_options": region_options,
            "selected_region": selected_region or "",
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "summary": summary,
            "region_chart": region_chart,
            "trend_chart": trend_chart,
            "records": records,
        },
    )


# Legacy redirects point to new subscriber overview
@router.get("/subscribers")
def subscribers_report_redirect():
    """Legacy subscriber report - redirect to overview."""
    return RedirectResponse(url="/admin/reports/subscribers/overview", status_code=302)


@router.get("/churn")
def churn_report_redirect():
    """Legacy churn report - redirect to churned subscribers."""
    return RedirectResponse(url="/admin/reports/subscribers/churned", status_code=302)


# =============================================================================
# Network Infrastructure Report (real data)
# =============================================================================


@router.get("/network", response_class=HTMLResponse)
def network_report(
    request: Request,
    db: Session = Depends(get_db),
    period_days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Network infrastructure report with real OLT/ONT/fiber data."""
    from app.services import network_reports as nr

    user = get_current_user(request)
    start_dt, end_dt = _parse_date_range(period_days, start_date, end_date)

    kpis = nr.get_network_kpis(db)
    olt_capacity = nr.get_olt_capacity(db)
    fiber_strand_status = nr.get_fiber_strand_status(db)
    ont_trend = nr.get_ont_activation_trend(db, start_dt, end_dt)
    olt_table = nr.get_olt_table(db)
    fdh_table = nr.get_fdh_utilization(db)
    fiber_inventory = nr.get_fiber_inventory(db)
    recent_ont = nr.get_recent_ont_activity(db)

    return templates.TemplateResponse(
        "admin/reports/network.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "network-report",
            "active_menu": "reports",
            "kpis": kpis,
            "olt_capacity": olt_capacity,
            "fiber_strand_status": fiber_strand_status,
            "ont_trend": ont_trend,
            "olt_table": olt_table,
            "fdh_table": fdh_table,
            "fiber_inventory": fiber_inventory,
            "recent_ont": recent_ont,
            "period_days": period_days,
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    )


@router.get("/network/export")
def network_report_export(
    db: Session = Depends(get_db),
):
    """Export network infrastructure report as CSV."""
    from app.services import network_reports as nr

    export_data = nr.get_network_export_data(db)
    filename = f"network_infrastructure_{datetime.now(UTC).strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Subscriber Overview Report
# =============================================================================


@router.get("/subscribers/overview", response_class=HTMLResponse)
def subscriber_overview(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    status: str | None = Query(None),
    region: str | None = Query(None),
):
    """Subscriber overview report."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    filter_opts = sr.overview_filter_options(db)
    region_options = filter_opts.get("regions", [])
    region_value = region if isinstance(region, str) else None
    status_value = status if isinstance(status, str) else None
    selected_region = region_value if region_value in region_options else None
    valid_statuses = {status.value: status for status in SubscriberStatus}
    selected_status = valid_statuses.get((status_value or "").strip().lower())
    subscriber_ids = sr.overview_filtered_subscriber_ids(db, status=selected_status, region=selected_region)

    kpis = sr.overview_kpis(db, start_dt, end_dt, subscriber_ids=subscriber_ids)
    growth_trend = sr.overview_growth_trend(db, start_dt, end_dt, subscriber_ids=subscriber_ids)
    status_dist = sr.overview_status_distribution(db, subscriber_ids=subscriber_ids)
    plan_dist = sr.overview_plan_distribution(db, subscriber_ids=subscriber_ids)
    regional = sr.overview_regional_breakdown(db, start_dt, end_dt, subscriber_ids=subscriber_ids)

    return templates.TemplateResponse(
        "admin/reports/subscriber_overview.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-overview",
            "active_menu": "reports",
            "kpis": kpis,
            "growth_trend": growth_trend,
            "status_dist": status_dist,
            "plan_dist": plan_dist,
            "regional": regional,
            "filter_opts": filter_opts,
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "selected_status": selected_status.value if selected_status else "",
            "selected_region": selected_region or "",
        },
    )


@router.get("/subscribers/overview/export")
def subscriber_overview_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    status: str | None = Query(None),
    region: str | None = Query(None),
):
    """Export subscriber overview as CSV."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    filter_opts = sr.overview_filter_options(db)
    region_options = filter_opts.get("regions", [])
    region_value = region if isinstance(region, str) else None
    status_value = status if isinstance(status, str) else None
    selected_region = region_value if region_value in region_options else None
    valid_statuses = {subscriber_status.value: subscriber_status for subscriber_status in SubscriberStatus}
    selected_status = valid_statuses.get((status_value or "").strip().lower())
    subscriber_ids = sr.overview_filtered_subscriber_ids(db, status=selected_status, region=selected_region)
    regional = sr.overview_regional_breakdown(db, start_dt, end_dt, subscriber_ids=subscriber_ids)

    export_data = [
        {
            "Region": r["region"],
            "Active": r["active"],
            "Suspended": r["suspended"],
            "Terminated": r["terminated"],
            "New in Period": r["new_in_period"],
            "Tickets": r["ticket_count"],
        }
        for r in regional
    ]
    filename = f"subscriber_overview_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Inactive Last 24h Report
# =============================================================================


@router.get("/subscribers/online-last-24h", response_class=HTMLResponse)
def subscriber_online_last_24h(
    request: Request,
    db: Session = Depends(get_db),
    status: str | None = Query(None),
    region: str | None = Query(None),
    search: str | None = Query(None),
    ticket_status: str | None = Query("all"),
    notification_state: str | None = Query("all"),
    activity_segment: str | None = Query("active_last24_not_online"),
    base_station: list[str] = Query(default=[]),
):
    """Subscribers with online/session activity in the last 24 hours."""
    from app.services import subscriber_notifications as subscriber_notifications_service
    from app.services import subscriber_offline_outreach as subscriber_offline_outreach_service
    from app.services import subscriber_reports as sr
    from app.services.crm.web_campaigns import outreach_channel_target_options

    user = get_current_user(request)
    filter_opts = sr.overview_filter_options(db)
    region_options = filter_opts.get("regions", [])
    status_value = (status or "").strip().lower()
    selected_region = region if isinstance(region, str) and region in region_options else None
    selected_status = next((item for item in SubscriberStatus if item.value == status_value), None)
    subscriber_ids = sr.overview_filtered_subscriber_ids(db, status=selected_status, region=selected_region)

    selected_ticket_status = (ticket_status or "all").strip().lower()
    valid_ticket_values = {item["value"] for item in _ONLINE_LAST_24H_TICKET_STATUS_OPTIONS}
    if selected_ticket_status not in valid_ticket_values:
        selected_ticket_status = "all"
    selected_notification_state = (notification_state or "all").strip().lower()
    valid_notification_values = {item["value"] for item in _ONLINE_LAST_24H_NOTIFICATION_STATE_OPTIONS}
    if selected_notification_state not in valid_notification_values:
        selected_notification_state = "all"
    selected_activity_segment = (activity_segment or "active_last24_not_online").strip().lower()
    valid_activity_segments = {item["value"] for item in _ONLINE_LAST_24H_ACTIVITY_SEGMENT_OPTIONS}
    if selected_activity_segment not in valid_activity_segments:
        selected_activity_segment = "active_last24_not_online"
    search_value = (search or "").strip()

    cache_key = _online_last_24h_cache_key(
        status=selected_status.value if selected_status else "",
        region=selected_region or "",
        search=search_value,
        ticket_status=selected_ticket_status,
        notification_state=selected_notification_state,
        activity_segment=selected_activity_segment,
        subscriber_ids=subscriber_ids,
    )
    online_customers, cache_hit = _online_last_24h_cached_rows(
        cache_key,
        lambda: _enrich_online_last_24h_campaign_status(
            subscriber_notifications_service.enrich_notification_rows(
                subscriber_offline_outreach_service.enrich_rows_with_station_status(
                    db,
                    sr.online_customers_last_24h_rows(
                        db,
                        subscriber_ids=subscriber_ids,
                        search=search_value,
                        ticket_status=selected_ticket_status,
                        notification_state=selected_notification_state,
                        activity_segment=selected_activity_segment,
                        limit=None,
                    ),
                )
                if hasattr(db, "execute")
                else sr.online_customers_last_24h_rows(
                    db,
                    subscriber_ids=subscriber_ids,
                    search=search_value,
                    ticket_status=selected_ticket_status,
                    notification_state=selected_notification_state,
                    activity_segment=selected_activity_segment,
                    limit=None,
                ),
                db,
            ),
            db,
        ),
    )
    if cache_hit:
        logger.info("online_last_24h_rows_cache_hit rows=%s", len(online_customers))
    base_station_options = _online_last_24h_base_station_options(online_customers)
    selected_base_stations = [
        value for value in _normalize_online_last_24h_base_station_values(base_station) if value in base_station_options
    ]
    online_customers = _filter_online_last_24h_base_stations(online_customers, selected_base_stations)
    online_customers = _filter_online_last_24h_notification_state(online_customers, selected_notification_state)
    online_customers = _sort_online_last_24h_rows(online_customers)
    has_db_session = hasattr(db, "execute")
    outreach_settings = (
        subscriber_offline_outreach_service.get_outreach_settings_snapshot(db)
        if has_db_session
        else {
            "enabled": False,
            "interval_seconds": 0,
            "local_time": "10:00",
            "timezone": "Africa/Lagos",
            "channel_target_id": "",
            "cooldown_hours": 0,
            "template_name": "",
            "template_language": "",
            "template_body": "",
            "template_parameter_values": {},
            "template_parameter_indexes": [],
            "template_payload": None,
        }
    )
    outreach_channel_targets = outreach_channel_target_options(db) if has_db_session else {}

    return templates.TemplateResponse(
        "admin/reports/subscriber_online_last_24h.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-online-last-24h",
            "active_menu": "reports",
            "online_customers": online_customers,
            "summary_total": len(online_customers),
            "summary_no_ticket": sum(1 for row in online_customers if not row.get("ticket_status")),
            "ticket_status_kpis": _online_last_24h_ticket_status_cards(online_customers),
            "filter_opts": filter_opts,
            "selected_status": selected_status.value if selected_status else "",
            "search": search_value,
            "selected_ticket_status": selected_ticket_status,
            "ticket_status_options": _ONLINE_LAST_24H_TICKET_STATUS_OPTIONS,
            "selected_notification_state": selected_notification_state,
            "notification_state_options": _ONLINE_LAST_24H_NOTIFICATION_STATE_OPTIONS,
            "selected_activity_segment": selected_activity_segment,
            "activity_segment_options": _ONLINE_LAST_24H_ACTIVITY_SEGMENT_OPTIONS,
            "base_station_options": base_station_options,
            "selected_base_stations": selected_base_stations,
            "selected_base_station_query": "".join(f"&base_station={quote(value)}" for value in selected_base_stations),
            "outreach_channel_targets": outreach_channel_targets,
            "outreach_settings": outreach_settings,
            "current_query": request.url.path + (f"?{request.url.query}" if request.url.query else ""),
        },
    )


@router.post("/subscribers/online-last-24h/outreach/settings")
def subscriber_online_last_24h_save_outreach_settings(
    request: Request,
    outreach_local_time: str = Form("10:00"),
    outreach_timezone: str = Form("Africa/Lagos"),
    outreach_channel_target_id: str = Form(""),
    outreach_whatsapp_template_name: str = Form(""),
    outreach_whatsapp_template_language: str = Form(""),
    outreach_whatsapp_template_parameters: str = Form("{}"),
    next_url: str = Form("/admin/reports/subscribers/online-last-24h"),
    db: Session = Depends(get_db),
):
    from app.services import subscriber_offline_outreach as subscriber_offline_outreach_service

    if not next_url.startswith("/admin/reports/subscribers/online-last-24h"):
        next_url = "/admin/reports/subscribers/online-last-24h"

    try:
        subscriber_offline_outreach_service.save_outreach_settings(
            db,
            local_time=outreach_local_time,
            timezone=outreach_timezone,
            channel_target_id=outreach_channel_target_id,
            whatsapp_template_name=outreach_whatsapp_template_name,
            whatsapp_template_language=outreach_whatsapp_template_language,
            whatsapp_template_parameters=outreach_whatsapp_template_parameters,
        )
    except Exception as exc:
        db.rollback()
        detail = getattr(exc, "detail", None) or str(exc)
        return _toast_redirect(next_url, message=str(detail), toast_type="error", status_code=303)

    return _toast_redirect(
        next_url,
        message="Offline outreach settings saved. The scheduler will check every 5 minutes and run once per day after the selected time.",
        toast_type="success",
        status_code=303,
    )


@router.get("/subscribers/online-last-24h/context/{subscriber_id}", response_class=JSONResponse)
def subscriber_online_last_24h_notify_context(
    subscriber_id: UUID,
    last_seen_at: str | None = Query(None),
    last_activity: str | None = Query(None),
    db: Session = Depends(get_db),
):
    from app.services import subscriber_notifications as subscriber_notifications_service

    payload = subscriber_notifications_service.notification_context_for_subscriber(
        db,
        subscriber_id=subscriber_id,
        last_seen_text=last_seen_at,
        last_activity=last_activity,
    )
    return JSONResponse(payload)


@router.post("/subscribers/online-last-24h/templates", response_class=JSONResponse)
def subscriber_online_last_24h_save_template(
    template_key: str = Form(...),
    email_subject: str = Form(...),
    email_body: str = Form(...),
    sms_body: str = Form(...),
    db: Session = Depends(get_db),
):
    from app.services import subscriber_notifications as subscriber_notifications_service

    saved = subscriber_notifications_service.save_template_bundle(
        db,
        template_key=template_key,
        email_subject=email_subject,
        email_body=email_body,
        sms_body=sms_body,
    )
    return JSONResponse({"ok": True, "template": saved})


@router.post("/subscribers/online-last-24h/notify")
def subscriber_online_last_24h_notify(
    request: Request,
    subscriber_id: UUID = Form(...),
    channel: str = Form(...),
    email_subject: str | None = Form(None),
    email_body: str | None = Form(None),
    sms_body: str | None = Form(None),
    scheduled_local_at: str | None = Form(None),
    next_url: str = Form("/admin/reports/subscribers/online-last-24h"),
    db: Session = Depends(get_db),
):
    from app.services import subscriber_notifications as subscriber_notifications_service

    if not next_url.startswith("/admin/reports/subscribers/online-last-24h"):
        next_url = "/admin/reports/subscribers/online-last-24h"

    user = get_current_user(request)
    raw_user_id = user.get("id")
    raw_person_id = user.get("person_id")

    try:
        subscriber_notifications_service.queue_subscriber_notification(
            db,
            subscriber_id=subscriber_id,
            channel_value=channel,
            email_subject=email_subject,
            email_body=email_body,
            sms_body=sms_body,
            scheduled_local_text=scheduled_local_at,
            sent_by_user_id=UUID(str(raw_user_id)) if raw_user_id else None,
            sent_by_person_id=UUID(str(raw_person_id)) if raw_person_id else None,
        )
    except Exception as exc:
        db.rollback()
        if isinstance(exc, Response):
            return exc
        detail = getattr(exc, "detail", None) or str(exc)
        return _toast_redirect(next_url, message=str(detail), toast_type="error", status_code=303)

    channel_label = channel.strip().lower()
    if channel_label == "both":
        message = "Email and WhatsApp notifications saved in test queue. No customer message was sent."
    elif channel_label == "whatsapp":
        message = "WhatsApp notification saved in test queue. No customer message was sent."
    else:
        message = "Email notification saved in test queue. No customer message was sent."
    return _toast_redirect(next_url, message=message)


@router.post("/subscribers/online-last-24h/notify/bulk")
def subscriber_online_last_24h_bulk_notify(
    request: Request,
    subscriber_ids: str = Form(...),
    channel: str = Form(...),
    email_subject: str | None = Form(None),
    email_body: str | None = Form(None),
    sms_body: str | None = Form(None),
    scheduled_local_at: str | None = Form(None),
    next_url: str = Form("/admin/reports/subscribers/online-last-24h"),
    db: Session = Depends(get_db),
):
    from app.services import subscriber_notifications as subscriber_notifications_service

    if not next_url.startswith("/admin/reports/subscribers/online-last-24h"):
        next_url = "/admin/reports/subscribers/online-last-24h"

    parsed_ids: list[UUID] = []
    for raw_id in subscriber_ids.split(","):
        try:
            parsed_ids.append(UUID(raw_id.strip()))
        except (TypeError, ValueError):
            continue
    if not parsed_ids:
        return _toast_redirect(next_url, message="Select at least one CRM-linked customer.", toast_type="error")

    user = get_current_user(request)
    raw_user_id = user.get("id")
    raw_person_id = user.get("person_id")
    result = subscriber_notifications_service.queue_bulk_subscriber_notifications(
        db,
        subscriber_ids=parsed_ids,
        channel_value=channel,
        email_subject=email_subject,
        email_body=email_body,
        sms_body=sms_body,
        scheduled_local_text=scheduled_local_at,
        sent_by_user_id=UUID(str(raw_user_id)) if raw_user_id else None,
        sent_by_person_id=UUID(str(raw_person_id)) if raw_person_id else None,
    )
    queued = int(result.get("queued", 0))
    skipped = int(result.get("skipped", 0))
    selected = int(result.get("selected", 0))
    toast_type = "success" if queued else "error"
    message = f"Bulk notification queued {queued} draft(s) for {selected} selected customer(s)."
    if skipped:
        message = (
            f"{message} Skipped {skipped} customer(s) due to missing contact details or recent duplicate notifications."
        )
    return _toast_redirect(next_url, message=message, toast_type=toast_type)


@router.post("/subscribers/online-last-24h/outreach")
def subscriber_online_last_24h_create_outreach(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form("Inactive Last 24H Outreach"),
    channel: str = Form("whatsapp"),
    channel_target_id: str = Form(""),
    subscriber_id: list[str] = Form(default=[]),
    next_url: str = Form("/admin/reports/subscribers/online-last-24h"),
):
    from app.services.crm.web_campaigns import create_online_last_24h_outreach_campaign

    if not next_url.startswith("/admin/reports/subscribers/online-last-24h"):
        next_url = "/admin/reports/subscribers/online-last-24h"

    selected_subscriber_ids: list[str] = []
    for raw_id in subscriber_id:
        try:
            selected_subscriber_ids.append(str(UUID(str(raw_id).strip())))
        except (TypeError, ValueError):
            continue
    if not selected_subscriber_ids:
        return _toast_redirect(next_url, message="Select at least one CRM-linked customer.", toast_type="error")
    allowed_target_ids = _online_last_24h_allowed_target_ids(db, channel)
    if str(channel_target_id or "").strip() not in allowed_target_ids:
        return _toast_redirect(
            next_url,
            message="Select an approved Send From target for this channel.",
            toast_type="error",
            status_code=303,
        )

    user = get_current_user(request)
    try:
        campaign = create_online_last_24h_outreach_campaign(
            db,
            name=name,
            channel=channel,
            channel_target_id=channel_target_id,
            subscriber_ids=selected_subscriber_ids,
            created_by_id=str(user.get("person_id") or "") or None,
            source_filters={
                "query": request.headers.get("referer", ""),
                "selected_count": len(selected_subscriber_ids),
                "source_report": "online_last_24h",
            },
        )
    except Exception as exc:
        db.rollback()
        detail = getattr(exc, "detail", None) or str(exc)
        return _toast_redirect(next_url, message=str(detail), toast_type="error", status_code=303)

    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign.id}", status_code=303)


@router.post("/subscribers/online-last-24h/notify/test-send")
def subscriber_online_last_24h_test_send(
    request: Request,
    subscriber_id: UUID = Form(...),
    next_url: str = Form("/admin/reports/subscribers/online-last-24h"),
    db: Session = Depends(get_db),
):
    from app.services import subscriber_notifications as subscriber_notifications_service

    if not next_url.startswith("/admin/reports/subscribers/online-last-24h"):
        next_url = "/admin/reports/subscribers/online-last-24h"

    user = get_current_user(request)
    raw_person_id = user.get("person_id")
    try:
        result = subscriber_notifications_service.approve_and_send_test_notifications(
            db,
            subscriber_id=subscriber_id,
            approved_by_person_id=UUID(str(raw_person_id)) if raw_person_id else None,
        )
    except Exception as exc:
        db.rollback()
        detail = getattr(exc, "detail", None) or str(exc)
        return _toast_redirect(next_url, message=str(detail), toast_type="error", status_code=303)

    sent = int(result.get("sent", 0))
    failed = int(result.get("failed", 0))
    toast_type = "success" if sent and not failed else "error"
    return _toast_redirect(
        next_url,
        message=f"Approve & Send submitted for test account: {sent} sent to outreach delivery, {failed} failed.",
        toast_type=toast_type,
    )


@router.get("/subscribers/online-last-24h/export")
def subscriber_online_last_24h_export(
    db: Session = Depends(get_db),
    status: str | None = Query(None),
    region: str | None = Query(None),
    search: str | None = Query(None),
    ticket_status: str | None = Query("all"),
    notification_state: str | None = Query("all"),
    activity_segment: str | None = Query("active_last24_not_online"),
    base_station: list[str] = Query(default=[]),
):
    """Export last-24h online subscribers report."""
    from app.services import subscriber_notifications as subscriber_notifications_service
    from app.services import subscriber_offline_outreach as subscriber_offline_outreach_service
    from app.services import subscriber_reports as sr

    filter_opts = sr.overview_filter_options(db)
    region_options = filter_opts.get("regions", [])
    status_value = (status or "").strip().lower()
    selected_region = region if isinstance(region, str) and region in region_options else None
    selected_status = next((item for item in SubscriberStatus if item.value == status_value), None)
    subscriber_ids = sr.overview_filtered_subscriber_ids(db, status=selected_status, region=selected_region)
    selected_ticket_status = (ticket_status or "all").strip().lower()
    valid_ticket_values = {item["value"] for item in _ONLINE_LAST_24H_TICKET_STATUS_OPTIONS}
    if selected_ticket_status not in valid_ticket_values:
        selected_ticket_status = "all"
    selected_notification_state = (notification_state or "all").strip().lower()
    valid_notification_values = {item["value"] for item in _ONLINE_LAST_24H_NOTIFICATION_STATE_OPTIONS}
    if selected_notification_state not in valid_notification_values:
        selected_notification_state = "all"
    selected_activity_segment = (activity_segment or "active_last24_not_online").strip().lower()
    valid_activity_segments = {item["value"] for item in _ONLINE_LAST_24H_ACTIVITY_SEGMENT_OPTIONS}
    if selected_activity_segment not in valid_activity_segments:
        selected_activity_segment = "active_last24_not_online"

    search_value = (search or "").strip()
    cache_key = _online_last_24h_cache_key(
        status=selected_status.value if selected_status else "",
        region=selected_region or "",
        search=search_value,
        ticket_status=selected_ticket_status,
        notification_state=selected_notification_state,
        activity_segment=selected_activity_segment,
        subscriber_ids=subscriber_ids,
    )
    online_customers, _cache_hit = _online_last_24h_cached_rows(
        cache_key,
        lambda: _enrich_online_last_24h_campaign_status(
            subscriber_notifications_service.enrich_notification_rows(
                subscriber_offline_outreach_service.enrich_rows_with_station_status(
                    db,
                    sr.online_customers_last_24h_rows(
                        db,
                        subscriber_ids=subscriber_ids,
                        search=search_value,
                        ticket_status=selected_ticket_status,
                        notification_state=selected_notification_state,
                        activity_segment=selected_activity_segment,
                        limit=None,
                    ),
                )
                if hasattr(db, "execute")
                else sr.online_customers_last_24h_rows(
                    db,
                    subscriber_ids=subscriber_ids,
                    search=search_value,
                    ticket_status=selected_ticket_status,
                    notification_state=selected_notification_state,
                    activity_segment=selected_activity_segment,
                    limit=None,
                ),
                db,
            ),
            db,
        ),
    )
    base_station_options = _online_last_24h_base_station_options(online_customers)
    selected_base_stations = [
        value for value in _normalize_online_last_24h_base_station_values(base_station) if value in base_station_options
    ]
    online_customers = _filter_online_last_24h_base_stations(online_customers, selected_base_stations)
    online_customers = _filter_online_last_24h_notification_state(online_customers, selected_notification_state)
    online_customers = _sort_online_last_24h_rows(online_customers)

    export_rows = [
        {
            "Name": row.get("name", ""),
            "Subscriber Number": row.get("subscriber_number", ""),
            "Status": row.get("status", ""),
            "Base Station": row.get("base_station", ""),
            "Email": row.get("email", ""),
            "Phone": row.get("phone", ""),
            "Last Seen At": row.get("last_seen_at", ""),
            "Last Activity": row.get("last_activity", ""),
            "Base Station Status": row.get("station_status", ""),
            "Currently Online": "Yes" if row.get("currently_online") else "No",
            "Ticket Status": row.get("ticket_status", ""),
        }
        for row in online_customers
    ]
    filename_prefix = (
        "active_last24_not_currently_online"
        if selected_activity_segment == "active_last24_not_online"
        else "online_customers_last_24h"
    )
    filename = f"{filename_prefix}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv"
    return _csv_response(export_rows, filename)


# =============================================================================
# Subscriber Lifecycle Report
# =============================================================================


@router.get("/subscribers/lifecycle", response_class=HTMLResponse)
def subscriber_lifecycle(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(0, ge=0, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    sort_by: str = Query("total_paid"),
):
    """Subscriber lifecycle and churn report."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)
    start_dt, end_dt = _resolve_lifecycle_date_range(db, days, start_date, end_date)

    kpis = sr.lifecycle_kpis(db, start_dt, end_dt)
    funnel = sr.lifecycle_funnel(db)
    churn_trend = sr.lifecycle_churn_trend(db)
    conversion_by_source = sr.lifecycle_conversion_by_source(db, start_dt, end_dt)
    retention_cohorts = sr.lifecycle_retention_cohorts(db, start_dt, end_dt)
    time_to_convert_distribution = sr.lifecycle_time_to_convert_distribution(db, start_dt, end_dt)
    plan_migration_flow = sr.lifecycle_plan_migration_flow(db, start_dt, end_dt)
    plan_distribution = sr.overview_plan_distribution(db, limit=8)
    recent_churns = sr.lifecycle_recent_churns(db)
    recent_churn_summary = sr.lifecycle_recent_churn_summary(db)
    longest_tenure = sr.lifecycle_longest_tenure(db)
    top_subscribers_by_value = sr.lifecycle_top_subscribers_by_value(db)
    top_subscribers_title = "Top Subscribers By Value (All Time)"
    top_subscribers_description = "Sorted by total paid across all subscriber histories."
    if sort_by == "tenure_months":
        top_subscribers_by_value = sorted(
            top_subscribers_by_value,
            key=lambda row: (-(row.get("tenure_months") or 0), -(row.get("total_paid") or 0), row.get("name") or ""),
        )
        top_subscribers_title = "By Tenure"
        top_subscribers_description = "Sorted by tenure, with total paid as tie-breaker."
    elif sort_by == "plan_type":
        top_subscribers_by_value = sorted(
            top_subscribers_by_value,
            key=lambda row: (
                (row.get("plan") or "").lower(),
                -(row.get("total_paid") or 0),
                -(row.get("tenure_months") or 0),
                row.get("name") or "",
            ),
        )
        top_subscribers_title = "Plan Type"
        top_subscribers_description = "Sorted alphabetically by plan type, with revenue and tenure as tie-breakers."
    else:
        sort_by = "total_paid"

    return templates.TemplateResponse(
        "admin/reports/subscriber_lifecycle.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-lifecycle",
            "active_menu": "reports",
            "kpis": kpis,
            "funnel": funnel,
            "churn_trend": churn_trend,
            "conversion_by_source": conversion_by_source,
            "retention_cohorts": retention_cohorts,
            "time_to_convert_distribution": time_to_convert_distribution,
            "plan_migration_flow": plan_migration_flow,
            "plan_distribution": plan_distribution,
            "recent_churns": recent_churns,
            "recent_churn_summary": recent_churn_summary,
            "longest_tenure": longest_tenure,
            "top_subscribers_by_value": top_subscribers_by_value,
            "top_subscribers_title": top_subscribers_title,
            "top_subscribers_description": top_subscribers_description,
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "sort_by": sort_by,
        },
    )


@router.get("/subscribers/lifecycle/export")
def subscriber_lifecycle_export(
    db: Session = Depends(get_db),
    days: int = Query(0, ge=0, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Export subscriber lifecycle data as CSV."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _resolve_lifecycle_date_range(db, days, start_date, end_date)
    recent_churns = sr.lifecycle_recent_churns(db, limit=100)

    export_data = [
        {
            "Name": c["name"],
            "Subscriber #": c["subscriber_number"],
            "Plan": c["plan"],
            "Region": c["region"],
            "Activated": c["activated_at"],
            "Terminated": c["terminated_at"],
            "Tenure (days)": c["tenure_days"],
        }
        for c in recent_churns
    ]
    filename = f"subscriber_lifecycle_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Churned Subscribers Report
# =============================================================================


@router.get("/subscribers/churned", response_class=HTMLResponse)
def churned_subscribers(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=0, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    behavioral_days: int = Query(60, ge=30, le=180),
):
    """Standard churned subscribers dashboard with KPIs, trend, and churn detail tables."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)
    start_dt, end_dt = _resolve_lifecycle_date_range(db, days, start_date, end_date)
    kpis = sr.churned_subscribers_kpis(db, start_dt, end_dt, behavioral_days=behavioral_days)
    churn_trend = sr.churned_subscribers_trend(db, start_dt, end_dt, behavioral_days=behavioral_days)
    churned_rows = sr.churned_subscribers_rows(db, start_dt, end_dt, limit=100, behavioral_days=behavioral_days)
    failed_payment_rows = sr.churned_failed_payment_rows(
        db,
        start_dt,
        end_dt,
        limit=50,
        behavioral_days=behavioral_days,
    )
    cancelled_rows = sr.churned_cancelled_rows(db, start_dt, end_dt, limit=50)
    inactive_usage_rows = sr.churned_inactive_usage_rows(db, end_dt, limit=50)

    churned_count = kpis.get("churned_count")
    if churned_count is None:
        churned_count = kpis.get("terminated_in_period")
    if churned_count is None:
        churned_count = len(churned_rows)
    kpis["churned_count"] = int(churned_count or 0)

    active_at_start = int(kpis.get("total_active_subscribers_start") or 0)
    kpis["churn_rate"] = round((kpis["churned_count"] / active_at_start) * 100, 1) if active_at_start > 0 else 0.0

    return templates.TemplateResponse(
        "admin/reports/churned_subscribers.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-churned",
            "active_menu": "reports",
            "kpis": kpis,
            "churn_trend": churn_trend,
            "churned_rows": churned_rows,
            "failed_payment_rows": failed_payment_rows,
            "cancelled_rows": cancelled_rows,
            "inactive_usage_rows": inactive_usage_rows,
            "distinct_churned_subscribers_count": kpis["churned_count"],
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "behavioral_days": behavioral_days,
        },
    )


@router.get("/subscribers/churned/export")
def churned_subscribers_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=0, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    behavioral_days: int = Query(60, ge=30, le=180),
):
    """Export churned subscriber rows as CSV for selected range."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _resolve_lifecycle_date_range(db, days, start_date, end_date)
    churned_rows = sr.churned_subscribers_rows(db, start_dt, end_dt, limit=1000, behavioral_days=behavioral_days)

    export_data = [
        {
            "Name": row["name"],
            "Subscriber #": row["subscriber_number"],
            "Plan": row["plan"],
            "Region": row["region"],
            "Activated": row["activated_at"],
            "Terminated": row["terminated_at"],
            "Tenure (days)": row["tenure_days"],
        }
        for row in churned_rows
    ]
    filename = f"subscriber_churned_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Subscriber Billing Risk Report
# =============================================================================


@router.get(
    "/subscribers/billing-risk",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission("reports:billing", "reports:subscribers", "reports"))],
)
def subscriber_billing_risk(
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
    overdue_invoice_days: int = Query(30, ge=1, le=180),
    high_balance_only: bool = Query(False),
    segment: str | None = Query(None),
    segments: list[str] = Query(default=[]),
    days_past_due: str | None = Query(None),
    bucket: str | None = Query("all"),
    search: str | None = Query(None),
    enterprise_only: bool = Query(False),
    customer_segment: str | None = Query(None),
    mrr_sort: str | None = Query(None),
):
    """Billing risk dashboard for blocked, overdue, and otherwise at-risk subscribers."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)

    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    mrr_sort_value = request.query_params.get("mrr_sort")
    normalized_mrr_sort = (
        (mrr_sort_value if mrr_sort_value is not None else (mrr_sort if isinstance(mrr_sort, str) else ""))
        .strip()
        .lower()
    )
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments, query_segment or segment
    )

    churn_rows = sr.get_churn_table(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        segments=selected_segments,
        days_past_due=query_days_past_due or days_past_due,
        source="splynx_live",
        limit=500,
        enrich_visible_rows=False,
    )
    selected_labels = _segment_labels(selected_segments)
    if selected_labels:
        churn_rows = [row for row in churn_rows if str(row.get("risk_segment") or "") in selected_labels]
    normalized_search = (search if isinstance(search, str) else "").strip().lower()
    if normalized_search:
        churn_rows = [
            row
            for row in churn_rows
            if normalized_search
            in " ".join(
                [
                    str(row.get("name") or ""),
                    str(row.get("subscriber_id") or ""),
                    str(row.get("phone") or ""),
                    str(row.get("city") or ""),
                    str(row.get("street") or ""),
                    str(row.get("area") or ""),
                    str(row.get("plan") or ""),
                ]
            ).lower()
        ]
    normalized_bucket = (bucket if isinstance(bucket, str) else "all").strip().lower()
    if normalized_bucket != "all":

        def _matches_bucket(row: dict) -> bool:
            value = row.get("blocked_for_days")
            if value is None:
                return False
            days = int(value)
            if normalized_bucket == "0-7":
                return 0 <= days <= 7
            if normalized_bucket == "8-30":
                return 8 <= days <= 30
            if normalized_bucket == "31-60":
                return 31 <= days <= 60
            if normalized_bucket == "61+":
                return days >= 61
            return True

        churn_rows = [row for row in churn_rows if _matches_bucket(row)]
    if normalized_mrr_sort == "desc":
        churn_rows.sort(key=lambda row: (-float(row.get("mrr_total") or 0), str(row.get("name") or "").casefold()))
    elif normalized_mrr_sort == "asc":
        churn_rows.sort(key=lambda row: (float(row.get("mrr_total") or 0), str(row.get("name") or "").casefold()))
    overdue_invoices = sr.get_overdue_invoices_table(
        db,
        min_days_past_due=overdue_invoice_days,
        limit=250,
    )
    kpis = sr.churn_risk_summary(churn_rows, overdue_invoices)
    segment_breakdown = sr.churn_risk_segment_breakdown(churn_rows)
    aging_buckets = sr.churn_risk_aging_buckets(churn_rows, due_soon_days=due_soon_days)

    export_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "bucket": bucket or "all",
            "search": search or "",
            "mrr_sort": normalized_mrr_sort,
        },
        doseq=True,
    )
    retention_tracker_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "bucket": bucket or "all",
            "search": search or "",
            "mrr_sort": normalized_mrr_sort,
        },
        doseq=True,
    )
    refresh_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segment": segment or "",
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due or "",
            "bucket": bucket or "all",
            "search": search or "",
            "mrr_sort": normalized_mrr_sort,
        },
        doseq=True,
    )
    segment_all_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "days_past_due": query_days_past_due or days_past_due or "",
            "bucket": bucket or "all",
            "search": search or "",
            "mrr_sort": normalized_mrr_sort,
        },
        doseq=True,
    )
    segment_due_soon_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "days_past_due": query_days_past_due or days_past_due or "",
            "bucket": bucket or "all",
            "search": search or "",
            "mrr_sort": normalized_mrr_sort,
            "segment": "overdue",
        },
        doseq=True,
    )
    segment_suspended_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "days_past_due": query_days_past_due or days_past_due or "",
            "bucket": bucket or "all",
            "search": search or "",
            "mrr_sort": normalized_mrr_sort,
            "segment": "suspended",
        },
        doseq=True,
    )
    return templates.TemplateResponse(
        "admin/reports/subscriber_billing_risk.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-billing-risk",
            "active_menu": "reports",
            "kpis": kpis,
            "segment_breakdown": segment_breakdown,
            "aging_buckets": aging_buckets,
            "churn_rows": churn_rows,
            "overdue_invoices": overdue_invoices,
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": high_balance_only,
            "selected_segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "export_query": export_query,
            "retention_tracker_query": retention_tracker_query,
            "refresh_query": refresh_query,
            "segment_all_query": segment_all_query,
            "segment_due_soon_query": segment_due_soon_query,
            "segment_suspended_query": segment_suspended_query,
            "last_synced_at": _latest_subscriber_sync_at(db),
            "billing_risk_cache": {"row_count": len(churn_rows)},
            "csrf_token": get_csrf_token(request),
            "refresh_started": request.query_params.get("refresh_started") == "1",
            "refresh_error": request.query_params.get("refresh_error"),
            "live_bucket": bucket or "all",
            "live_search": search or "",
            "live_mrr_sort": normalized_mrr_sort,
            "enterprise_mrr_threshold": 70000,
        },
    )


@router.post("/subscribers/billing-risk/refresh")
def subscriber_billing_risk_refresh(
    request: Request,
    next_url: str = Form("/admin/reports/subscribers/billing-risk"),
    _permission: dict = Depends(require_any_permission("reports:billing", "reports:subscribers", "reports")),
):
    if not next_url.startswith("/admin/reports/subscribers/billing-risk"):
        next_url = "/admin/reports/subscribers/billing-risk"

    try:
        sync_subscribers_from_splynx.delay()
        return RedirectResponse(url=_append_query_flag(next_url, "refresh_started", "1"), status_code=303)
    except Exception:
        logger.exception("Failed to enqueue Splynx subscriber sync")
        return RedirectResponse(url=_append_query_flag(next_url, "refresh_error", "queue_unavailable"), status_code=303)


@router.get(
    "/subscribers/billing-risk/export",
    dependencies=[Depends(require_any_permission("reports:billing", "reports:subscribers", "reports"))],
)
def subscriber_billing_risk_export(
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
    high_balance_only: bool = Query(False),
    segment: str | None = Query(None),
    segments: list[str] = Query(default=[]),
    days_past_due: str | None = Query(None),
    enterprise_only: bool = Query(False),
    customer_segment: str | None = Query(None),
    mrr_sort: str | None = Query(None),
):
    """Export billing risk rows as CSV."""
    from app.services import subscriber_reports as sr

    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    mrr_sort_value = request.query_params.get("mrr_sort")
    normalized_mrr_sort = (
        (mrr_sort_value if mrr_sort_value is not None else (mrr_sort if isinstance(mrr_sort, str) else ""))
        .strip()
        .lower()
    )
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments, query_segment or segment
    )

    churn_rows = sr.get_churn_table(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        segments=selected_segments,
        days_past_due=query_days_past_due or days_past_due,
        source="splynx_live",
        limit=2000,
    )
    selected_labels = _segment_labels(selected_segments)
    if selected_labels:
        churn_rows = [row for row in churn_rows if str(row.get("risk_segment") or "") in selected_labels]
    if normalized_mrr_sort == "desc":
        churn_rows.sort(key=lambda row: (-float(row.get("mrr_total") or 0), str(row.get("name") or "").casefold()))
    elif normalized_mrr_sort == "asc":
        churn_rows.sort(key=lambda row: (float(row.get("mrr_total") or 0), str(row.get("name") or "").casefold()))
    export_data = [
        {
            "Name": row["name"],
            "Email": row["email"],
            "Phone": row.get("phone", ""),
            "Subscriber Status": row["subscriber_status"],
            "Risk Segment": row["risk_segment"],
            "Next Bill Date": row["next_bill_date"],
            "Days To Due": row["days_to_due"],
            "Days Past Due": row.get("days_past_due", ""),
            "Balance": row["balance"],
            "Billing Cycle": row["billing_cycle"],
            "Last Transaction Date": row["last_transaction_date"],
            "Expires In": row["expires_in"],
            "Invoiced Until": row["invoiced_until"],
            "Days Since Last Payment": row.get("days_since_last_payment", ""),
            "Total Paid": row["total_paid"],
            "High Balance Risk": "Yes" if row["is_high_balance_risk"] else "No",
        }
        for row in churn_rows
    ]
    filename = f"subscriber_billing_risk_{datetime.now(UTC).strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Subscriber Service Quality Report
# =============================================================================


@router.get("/subscribers/service-quality", response_class=HTMLResponse)
def subscriber_service_quality(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Subscriber service quality report."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    kpis = sr.service_quality_kpis(db, start_dt, end_dt)
    tickets_by_type = sr.service_quality_tickets_by_type(db, start_dt, end_dt)
    wo_by_type = sr.service_quality_wo_by_type(db, start_dt, end_dt)
    weekly_trend = sr.service_quality_weekly_trend(db, start_dt, end_dt)
    high_maintenance = sr.service_quality_high_maintenance(db, start_dt, end_dt)
    regional_quality = sr.service_quality_regional(db, start_dt, end_dt)

    return templates.TemplateResponse(
        "admin/reports/subscriber_service_quality.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-service-quality",
            "active_menu": "reports",
            "kpis": kpis,
            "tickets_by_type": tickets_by_type,
            "wo_by_type": wo_by_type,
            "weekly_trend": weekly_trend,
            "high_maintenance": high_maintenance,
            "regional_quality": regional_quality,
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    )


@router.get("/subscribers/service-quality/export")
def subscriber_service_quality_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Export service quality data as CSV."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    high_maintenance = sr.service_quality_high_maintenance(db, start_dt, end_dt, limit=100)

    export_data = [
        {
            "Name": h["name"],
            "Subscriber #": h["subscriber_number"],
            "Region": h["region"],
            "Plan": h["plan"],
            "Tickets": h["tickets"],
            "Work Orders": h["work_orders"],
            "Projects": h["projects"],
            "Total Issues": h["total"],
        }
        for h in high_maintenance
    ]
    filename = f"service_quality_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Subscriber Revenue & Pipeline Report
# =============================================================================


@router.get("/subscribers/revenue", response_class=HTMLResponse)
def subscriber_revenue(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Subscriber revenue and pipeline report."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    kpis = sr.revenue_kpis(db, start_dt, end_dt)
    monthly_trend = sr.revenue_monthly_trend(db)
    payment_status = sr.revenue_payment_status(db, start_dt, end_dt)
    order_status = sr.revenue_order_status(db, start_dt, end_dt)
    top_subscribers = sr.revenue_top_subscribers(db, start_dt, end_dt)
    outstanding = sr.revenue_outstanding_balances(db)

    return templates.TemplateResponse(
        "admin/reports/subscriber_revenue.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-revenue",
            "active_menu": "reports",
            "kpis": kpis,
            "monthly_trend": monthly_trend,
            "payment_status": payment_status,
            "order_status": order_status,
            "top_subscribers": top_subscribers,
            "outstanding": outstanding,
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    )


@router.get("/subscribers/revenue/export")
def subscriber_revenue_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Export revenue data as CSV."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    top_subs = sr.revenue_top_subscribers(db, start_dt, end_dt, limit=100)

    export_data = [
        {
            "Name": s["name"],
            "Email": s["email"],
            "Total Revenue": s["total_revenue"],
            "Order Count": s["order_count"],
            "Avg Order Value": s["avg_value"],
            "Latest Order": s["latest_order"],
            "Status": s["status"],
        }
        for s in top_subs
    ]
    filename = f"subscriber_revenue_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Technician Performance Report
# =============================================================================


def _get_technician_stats(
    db: Session,
    start_date: datetime,
    end_date: datetime,
) -> tuple[list[dict[str, object]], int, dict[str, int], list[WorkOrder]]:
    """Get technician performance stats for a date range."""
    # Active technician profiles should appear even when they have no jobs in range.
    active_technician_person_ids = {
        row
        for row in db.scalars(select(TechnicianProfile.person_id).where(TechnicianProfile.is_active.is_(True))).all()
        if row is not None
    }

    total_rows = db.execute(
        select(WorkOrder.assigned_to_person_id, func.count(WorkOrder.id))
        .where(
            WorkOrder.is_active.is_(True),
            WorkOrder.assigned_to_person_id.isnot(None),
            WorkOrder.created_at >= start_date,
            WorkOrder.created_at <= end_date,
        )
        .group_by(WorkOrder.assigned_to_person_id)
    ).all()
    completed_rows = db.execute(
        select(WorkOrder.assigned_to_person_id, func.count(WorkOrder.id))
        .where(
            WorkOrder.is_active.is_(True),
            WorkOrder.assigned_to_person_id.isnot(None),
            WorkOrder.status == WorkOrderStatus.completed,
            WorkOrder.completed_at >= start_date,
            WorkOrder.completed_at <= end_date,
        )
        .group_by(WorkOrder.assigned_to_person_id)
    ).all()

    total_by_person = {person_id: count for person_id, count in total_rows if person_id is not None}
    completed_by_person = {person_id: count for person_id, count in completed_rows if person_id is not None}

    person_ids = set(active_technician_person_ids) | set(total_by_person.keys()) | set(completed_by_person.keys())
    people_by_id: dict = {}
    if person_ids:
        people = db.scalars(select(Person).where(Person.id.in_(person_ids), Person.is_active.is_(True))).all()
        people_by_id = {person.id: person for person in people}

    def _person_name(person: Person | None) -> str:
        if not person:
            return "Unknown"
        if person.display_name:
            return person.display_name
        return f"{person.first_name or ''} {person.last_name or ''}".strip() or "Unknown"

    technician_stats = []
    for person_id in person_ids:
        total_assigned = int(total_by_person.get(person_id, 0))
        completed = int(completed_by_person.get(person_id, 0))
        completion_rate = (completed / total_assigned * 100) if total_assigned > 0 else 0
        rating = min(5, max(1, int(completion_rate / 20))) if total_assigned > 0 else 3
        technician_stats.append(
            {
                "name": _person_name(people_by_id.get(person_id)),
                "total_jobs": total_assigned,
                "completed_jobs": completed,
                "avg_hours": 2.5 if completed > 0 else 0,  # Placeholder: use time tracking when available
                "rating": rating,
                "completion_rate": round(completion_rate, 1),
            }
        )

    technician_stats.sort(
        key=lambda x: (
            -(x["completed_jobs"] if isinstance(x["completed_jobs"], int) else 0),
            -(x["total_jobs"] if isinstance(x["total_jobs"], int) else 0),
            str(x.get("name", "")).lower(),
        )
    )
    total_jobs_completed = sum(completed_by_person.values())

    # Job type breakdown
    type_rows = db.execute(
        select(WorkOrder.work_type, func.count(WorkOrder.id))
        .where(
            WorkOrder.is_active.is_(True),
            WorkOrder.created_at >= start_date,
            WorkOrder.created_at <= end_date,
        )
        .group_by(WorkOrder.work_type)
    ).all()
    job_type_breakdown: dict[str, int] = {
        (work_type.value if work_type else "other"): count for work_type, count in type_rows
    }

    # Recent completions
    recent_completions = (
        db.scalars(
            select(WorkOrder)
            .options(joinedload(WorkOrder.assigned_to))
            .where(
                WorkOrder.is_active.is_(True),
                WorkOrder.status == WorkOrderStatus.completed,
                WorkOrder.completed_at >= start_date,
                WorkOrder.completed_at <= end_date,
            )
            .order_by(WorkOrder.completed_at.desc())
            .limit(5)
        )
        .unique()
        .all()
    )

    return technician_stats, total_jobs_completed, job_type_breakdown, list(recent_completions)


@router.get("/technician", response_class=HTMLResponse)
def technician_report(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Technician performance report."""
    user = get_current_user(request)

    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    technician_stats, total_jobs_completed, job_type_breakdown, recent_completions = _get_technician_stats(
        db, start_dt, end_dt
    )

    # Summary stats
    avg_completion_hours = 2.5  # Placeholder
    first_visit_rate = 85.0  # Placeholder

    return templates.TemplateResponse(
        "admin/reports/technician.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "total_technicians": len(technician_stats),
            "jobs_completed": total_jobs_completed,
            "avg_completion_hours": avg_completion_hours,
            "first_visit_rate": first_visit_rate,
            "technician_stats": technician_stats,
            "job_type_breakdown": job_type_breakdown,
            "recent_completions": recent_completions,
            "days": days,
            "start_date": start_dt.strftime("%Y-%m-%d"),
            "end_date": end_dt.strftime("%Y-%m-%d"),
        },
    )


@router.get("/technician/export")
def technician_report_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Export technician performance report as CSV."""
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    technician_stats, _, _, _ = _get_technician_stats(db, start_dt, end_dt)

    # Format for CSV
    export_data = []
    for i, tech in enumerate(technician_stats, 1):
        export_data.append(
            {
                "Rank": i,
                "Technician": tech["name"],
                "Total Jobs": tech["total_jobs"],
                "Completed Jobs": tech["completed_jobs"],
                "Completion Rate (%)": tech["completion_rate"],
                "Avg Hours": tech["avg_hours"],
                "Rating": tech["rating"],
            }
        )

    filename = f"technician_performance_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Project Task People Performance Report
# =============================================================================


def _hours_between(start_at: datetime | None, end_at: datetime | None) -> float | None:
    if not start_at or not end_at:
        return None
    if start_at.tzinfo is None and end_at.tzinfo is not None:
        start_at = start_at.replace(tzinfo=end_at.tzinfo)
    elif start_at.tzinfo is not None and end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=start_at.tzinfo)
    return max((end_at - start_at).total_seconds() / 3600, 0.0)


def _datetime_after(left: datetime | None, right: datetime | None) -> bool:
    if not left or not right:
        return False
    if left.tzinfo is None and right.tzinfo is not None:
        left = left.replace(tzinfo=right.tzinfo)
    elif left.tzinfo is not None and right.tzinfo is None:
        right = right.replace(tzinfo=left.tzinfo)
    return left > right


def _project_task_person_name(person: Person | None) -> str:
    if not person:
        return "Unknown"
    if person.display_name:
        return person.display_name
    return f"{person.first_name or ''} {person.last_name or ''}".strip() or "Unknown"


def _task_assignee_ids(task: ProjectTask) -> list[UUID]:
    assignee_ids = [assignee.person_id for assignee in task.assignees if assignee.person_id]
    if assignee_ids:
        return list(dict.fromkeys(assignee_ids))
    if task.assigned_to_person_id:
        return [task.assigned_to_person_id]
    return []


def _new_project_task_person_accumulator(
    person_id: UUID,
    people_by_id: dict[UUID, Person],
) -> _ProjectTaskPersonAccumulator:
    return {
        "id": str(person_id),
        "name": _project_task_person_name(people_by_id.get(person_id)),
        "assigned_tasks": 0,
        "completed_tasks": 0,
        "open_tasks": 0,
        "blocked_tasks": 0,
        "overdue_tasks": 0,
        "on_time_tasks": 0,
        "cycle_hours_total": 0.0,
        "cycle_hours_count": 0,
        "effort_accuracy_total": 0.0,
        "effort_accuracy_count": 0,
    }


def _metric_int(row: dict[str, object], key: str) -> int:
    value = row.get(key, 0)
    return int(value) if isinstance(value, int | float | str) else 0


def _metric_float(row: dict[str, object], key: str) -> float:
    value = row.get(key, 0.0)
    return float(value) if isinstance(value, int | float | str) else 0.0


def _project_task_window_clause(start_date: datetime, end_date: datetime):
    """Select tasks that were active at any point in the requested window."""
    return and_(
        ProjectTask.created_at <= end_date,
        or_(ProjectTask.completed_at.is_(None), ProjectTask.completed_at >= start_date),
    )


def _get_project_task_people_performance(
    db: Session,
    start_date: datetime,
    end_date: datetime,
) -> tuple[list[dict[str, object]], dict[str, object], dict[str, int], list[ProjectTask]]:
    """Aggregate people performance from project task assignment activity."""
    tasks = (
        db.scalars(
            select(ProjectTask)
            .options(
                selectinload(ProjectTask.assignees),
                selectinload(ProjectTask.project),
            )
            .where(
                ProjectTask.is_active.is_(True),
                _project_task_window_clause(start_date, end_date),
            )
        )
        .unique()
        .all()
    )

    person_ids = {person_id for task in tasks for person_id in _task_assignee_ids(task)}
    if not person_ids:
        person_ids.update(
            row
            for row in db.scalars(
                select(ProjectTask.assigned_to_person_id)
                .where(
                    ProjectTask.is_active.is_(True),
                    ProjectTask.assigned_to_person_id.isnot(None),
                    ProjectTask.completed_at >= start_date,
                    ProjectTask.completed_at <= end_date,
                )
                .distinct()
            ).all()
            if row is not None
        )
        person_ids.update(
            row
            for row in db.scalars(
                select(ProjectTaskAssignee.person_id)
                .join(ProjectTask, ProjectTask.id == ProjectTaskAssignee.task_id)
                .where(
                    ProjectTask.is_active.is_(True),
                    ProjectTask.completed_at >= start_date,
                    ProjectTask.completed_at <= end_date,
                )
                .distinct()
            ).all()
            if row is not None
        )

    people_by_id: dict[UUID, Person] = {}
    if person_ids:
        people = db.scalars(select(Person).where(Person.id.in_(person_ids), Person.is_active.is_(True))).all()
        people_by_id = {person.id: person for person in people}

    stats_by_person: dict[UUID, _ProjectTaskPersonAccumulator] = {
        person_id: _new_project_task_person_accumulator(person_id, people_by_id) for person_id in person_ids
    }

    project_type_breakdown: dict[str, int] = {}
    now = datetime.now(UTC)

    for task in tasks:
        project_type = task.project.project_type.value if task.project and task.project.project_type else "unspecified"
        project_type_breakdown[project_type] = project_type_breakdown.get(project_type, 0) + 1

        assignee_ids = _task_assignee_ids(task)
        if not assignee_ids:
            continue

        is_done = bool(
            task.status == TaskStatus.done
            and task.completed_at
            and not _datetime_after(start_date, task.completed_at)
            and not _datetime_after(task.completed_at, end_date)
        )
        is_blocked = task.status == TaskStatus.blocked
        completed_or_window_end = task.completed_at or now
        if _datetime_after(completed_or_window_end, end_date):
            completed_or_window_end = end_date
        is_overdue = bool(task.due_at and _datetime_after(completed_or_window_end, task.due_at) and not is_done)
        is_on_time = bool(
            is_done and task.due_at and task.completed_at and not _datetime_after(task.completed_at, task.due_at)
        )
        cycle_hours = _hours_between(task.start_at or task.created_at, task.completed_at) if is_done else None
        effort_accuracy = None
        if cycle_hours is not None and task.effort_hours and task.effort_hours > 0:
            effort_accuracy = max(0.0, 1 - abs(cycle_hours - float(task.effort_hours)) / float(task.effort_hours)) * 100

        for person_id in assignee_ids:
            row = stats_by_person.setdefault(
                person_id,
                _new_project_task_person_accumulator(person_id, people_by_id),
            )
            row["assigned_tasks"] += 1
            if is_done:
                row["completed_tasks"] += 1
            else:
                row["open_tasks"] += 1
            if is_blocked:
                row["blocked_tasks"] += 1
            if is_overdue:
                row["overdue_tasks"] += 1
            if is_on_time:
                row["on_time_tasks"] += 1
            if cycle_hours is not None:
                row["cycle_hours_total"] += cycle_hours
                row["cycle_hours_count"] += 1
            if effort_accuracy is not None:
                row["effort_accuracy_total"] += effort_accuracy
                row["effort_accuracy_count"] += 1

    rows: list[dict[str, object]] = []
    for row in stats_by_person.values():
        assigned = int(row["assigned_tasks"])
        completed = int(row["completed_tasks"])
        blocked = int(row["blocked_tasks"])
        overdue = int(row["overdue_tasks"])
        completion_rate = (completed / assigned * 100) if assigned else 0.0
        on_time_rate = (int(row["on_time_tasks"]) / completed * 100) if completed else 0.0
        blocked_rate = (blocked / assigned * 100) if assigned else 0.0
        overdue_rate = (overdue / assigned * 100) if assigned else 0.0
        avg_cycle_hours = (
            float(row["cycle_hours_total"]) / int(row["cycle_hours_count"]) if int(row["cycle_hours_count"]) else 0.0
        )
        effort_accuracy = (
            float(row["effort_accuracy_total"]) / int(row["effort_accuracy_count"])
            if int(row["effort_accuracy_count"])
            else 0.0
        )
        health_score = max(0.0, 100.0 - blocked_rate - overdue_rate)
        performance_score = (
            (completion_rate * 0.4) + (on_time_rate * 0.35) + (health_score * 0.15) + (effort_accuracy * 0.10)
        )
        rows.append(
            {
                "id": row["id"],
                "name": row["name"],
                "assigned_tasks": assigned,
                "completed_tasks": completed,
                "open_tasks": int(row["open_tasks"]),
                "blocked_tasks": blocked,
                "overdue_tasks": overdue,
                "completion_rate": round(completion_rate, 1),
                "on_time_rate": round(on_time_rate, 1),
                "avg_cycle_hours": round(avg_cycle_hours, 1),
                "effort_accuracy": round(effort_accuracy, 1),
                "performance_score": round(performance_score, 1),
                "rating": min(5, max(1, round(performance_score / 20))) if assigned else 3,
            }
        )

    rows.sort(
        key=lambda item: (
            -_metric_float(item, "performance_score"),
            -_metric_int(item, "completed_tasks"),
            str(item.get("name", "")).lower(),
        )
    )

    completed_rows = [row for row in rows if _metric_int(row, "completed_tasks") > 0]
    total_assigned = sum(_metric_int(row, "assigned_tasks") for row in rows)
    total_completed = sum(_metric_int(row, "completed_tasks") for row in rows)
    total_overdue = sum(_metric_int(row, "overdue_tasks") for row in rows)
    weighted_completion = (total_completed / total_assigned * 100) if total_assigned else 0.0
    weighted_on_time = (
        sum(_metric_int(row, "completed_tasks") * _metric_float(row, "on_time_rate") for row in rows) / total_completed
        if total_completed
        else 0.0
    )
    avg_cycle_hours = (
        sum(_metric_int(row, "completed_tasks") * _metric_float(row, "avg_cycle_hours") for row in completed_rows)
        / total_completed
        if total_completed
        else 0.0
    )
    summary: dict[str, object] = {
        "people_count": len(rows),
        "tasks_assigned": total_assigned,
        "tasks_completed": total_completed,
        "tasks_overdue": total_overdue,
        "completion_rate": round(weighted_completion, 1),
        "on_time_rate": round(weighted_on_time, 1),
        "avg_cycle_hours": round(avg_cycle_hours, 1),
    }

    recent_completions = (
        db.scalars(
            select(ProjectTask)
            .options(
                joinedload(ProjectTask.assigned_to),
                selectinload(ProjectTask.assignees).selectinload(ProjectTaskAssignee.person),
                selectinload(ProjectTask.project),
            )
            .where(
                ProjectTask.is_active.is_(True),
                ProjectTask.status == TaskStatus.done,
                ProjectTask.completed_at >= start_date,
                ProjectTask.completed_at <= end_date,
            )
            .order_by(ProjectTask.completed_at.desc())
            .limit(5)
        )
        .unique()
        .all()
    )

    return rows, summary, project_type_breakdown, list(recent_completions)


@router.get("/project-task-performance", response_class=HTMLResponse)
def project_task_people_performance_report(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """People performance report based on assigned project tasks."""
    user = get_current_user(request)
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    people_stats, summary, project_type_breakdown, recent_completions = _get_project_task_people_performance(
        db, start_dt, end_dt
    )

    return templates.TemplateResponse(
        "admin/reports/project_task_performance.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "project-task-performance",
            "active_menu": "reports",
            "people_stats": people_stats,
            "summary": summary,
            "project_type_breakdown": project_type_breakdown,
            "recent_completions": recent_completions,
            "days": days,
            "custom_range": bool(start_date and end_date),
            "start_date": start_dt.strftime("%Y-%m-%d"),
            "end_date": end_dt.strftime("%Y-%m-%d"),
        },
    )


@router.get("/project-task-performance/export")
def project_task_people_performance_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Export project task people performance report as CSV."""
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    people_stats, _, _, _ = _get_project_task_people_performance(db, start_dt, end_dt)

    export_data = []
    for index, person in enumerate(people_stats, 1):
        export_data.append(
            {
                "Rank": index,
                "Person": person["name"],
                "Assigned Tasks": person["assigned_tasks"],
                "Completed Tasks": person["completed_tasks"],
                "Open Tasks": person["open_tasks"],
                "Blocked Tasks": person["blocked_tasks"],
                "Overdue Tasks": person["overdue_tasks"],
                "Completion Rate (%)": person["completion_rate"],
                "On-Time Rate (%)": person["on_time_rate"],
                "Avg Cycle Hours": person["avg_cycle_hours"],
                "Effort Accuracy (%)": person["effort_accuracy"],
                "Performance Score": person["performance_score"],
            }
        )

    filename = f"project_task_people_performance_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# CRM Performance Report
# =============================================================================


@router.get("/crm-performance", response_class=HTMLResponse)
def crm_performance_report(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
    agent_id: str | None = Query(None),
    team_id: str | None = Query(None),
    channel_type: str | None = Query(None),
):
    """CRM agent/team performance report."""
    from app.models.crm.enums import ChannelType

    user = get_current_user(request)
    now = datetime.now(UTC)
    start_date = now - timedelta(days=days)

    # Get inbox KPIs
    inbox_stats = crm_reports_service.inbox_kpis(
        db=db,
        start_at=start_date,
        end_at=now,
        channel_type=channel_type,
        agent_id=agent_id,
        team_id=team_id,
    )

    # Get per-agent performance metrics
    agent_stats = crm_reports_service.agent_performance_metrics(
        db=db,
        start_at=start_date,
        end_at=now,
        agent_id=agent_id,
        team_id=team_id,
        channel_type=channel_type,
    )

    # Get conversation trend data
    trend_data = crm_reports_service.conversation_trend(
        db=db,
        start_at=start_date,
        end_at=now,
        agent_id=agent_id,
        team_id=team_id,
        channel_type=channel_type,
    )

    # Summary stats
    total_conversations = sum(agent["total_conversations"] for agent in agent_stats)
    resolved_conversations = sum(agent["resolved_conversations"] for agent in agent_stats)
    resolution_rate = resolved_conversations / total_conversations * 100 if total_conversations > 0 else 0

    # Weighted average FRT across agents (weight by total conversations with valid FRT)
    total_team_response_minutes = sum(
        (a["avg_first_response_minutes"] or 0) * a["total_conversations"]
        for a in agent_stats
        if a["avg_first_response_minutes"] is not None
    )
    total_convos_with_frt = sum(
        a["total_conversations"] for a in agent_stats if a["avg_first_response_minutes"] is not None
    )
    avg_frt = total_team_response_minutes / total_convos_with_frt if total_convos_with_frt > 0 else None

    # Weighted average resolution time across agents (weight by resolved conversations)
    total_resolution_minutes = sum(
        (a["avg_resolution_minutes"] or 0) * a["resolved_conversations"]
        for a in agent_stats
        if a["avg_resolution_minutes"] is not None
    )
    avg_resolution_time = total_resolution_minutes / resolved_conversations if resolved_conversations > 0 else None

    # Get teams and agents for filter dropdowns
    teams = crm_team_service.Teams.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    agents = crm_team_service.Agents.list(
        db=db,
        person_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    agent_labels = crm_team_service.get_agent_labels(db, agents)

    # Channel type breakdown (ensure key channels appear even with zero data)
    channel_breakdown = inbox_stats.get("messages", {}).get("by_channel", {})
    channel_labels: dict[str, str] = {}
    email_inbox_breakdown = inbox_stats.get("messages", {}).get("by_email_inbox", {}) or {}

    if email_inbox_breakdown:
        channel_breakdown.pop(str(ChannelType.email), None)
        for inbox_id, data in email_inbox_breakdown.items():
            inbox_key = f"email:{inbox_id}"
            channel_breakdown[inbox_key] = data.get("count", 0)
            inbox_label = data.get("label") or "Unknown Inbox"
            channel_labels[inbox_key] = f"Email - {inbox_label}"

    for channel in (ChannelType.whatsapp, ChannelType.facebook_messenger, ChannelType.instagram_dm):
        channel_key = str(channel)
        if channel_key not in channel_breakdown:
            channel_breakdown[channel_key] = 0

    return templates.TemplateResponse(
        "admin/reports/crm_performance.html",
        {
            "request": request,
            "user": user,
            "active_page": "crm-performance",
            "active_menu": "reports",
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            # Summary metrics
            "total_conversations": total_conversations,
            "resolved_conversations": resolved_conversations,
            "resolution_rate": resolution_rate,
            "avg_frt_minutes": avg_frt,
            "avg_resolution_minutes": avg_resolution_time,
            "total_messages": inbox_stats.get("messages", {}).get("total", 0),
            "inbound_messages": inbox_stats.get("messages", {}).get("inbound", 0),
            "outbound_messages": inbox_stats.get("messages", {}).get("outbound", 0),
            # Agent breakdown
            "agent_stats": agent_stats,
            # Trend data for charts
            "trend_data": trend_data,
            # Channel breakdown
            "channel_breakdown": channel_breakdown,
            "channel_labels": channel_labels,
            # Filters
            "days": days,
            "selected_agent_id": agent_id,
            "selected_team_id": team_id,
            "selected_channel_type": channel_type,
            # Dropdown options
            "teams": teams,
            "agents": agents,
            "agent_labels": agent_labels,
            "channel_types": [t.value for t in ChannelType],
        },
    )


@router.get("/crm-performance/export")
def crm_performance_report_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    agent_id: str | None = Query(None),
    team_id: str | None = Query(None),
    channel_type: str | None = Query(None),
):
    """Export CRM performance report as CSV."""
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    # Get per-agent performance metrics
    agent_stats = crm_reports_service.agent_performance_metrics(
        db=db,
        start_at=start_dt,
        end_at=end_dt,
        agent_id=agent_id,
        team_id=team_id,
        channel_type=channel_type,
    )

    # Format for CSV
    export_data = []
    for i, agent in enumerate(agent_stats, 1):
        resolution_rate = (
            agent["resolved_conversations"] / agent["total_conversations"] * 100
            if agent["total_conversations"] > 0
            else 0
        )
        export_data.append(
            {
                "Rank": i,
                "Agent": agent["name"],
                "Active Hours": agent.get("active_hours_display") or "",
                "Total Conversations": agent["total_conversations"],
                "Resolved": agent["resolved_conversations"],
                "Resolution Rate (%)": round(resolution_rate, 1),
                "Avg First Response (min)": round(agent["avg_first_response_minutes"], 1)
                if agent["avg_first_response_minutes"]
                else "",
                "Avg Resolution Time (min)": round(agent["avg_resolution_minutes"], 1)
                if agent["avg_resolution_minutes"]
                else "",
            }
        )

    filename = f"crm_performance_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Agent Performance Report (Weekly Trends)
# =============================================================================


@router.get("/agent-performance", response_class=HTMLResponse)
def agent_performance_report(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(7, ge=7, le=90),
):
    """Weekly agent performance report with trend comparisons."""
    user = get_current_user(request)
    now = datetime.now(UTC)
    current_start = now - timedelta(days=days)
    previous_start = current_start - timedelta(days=days)
    previous_end = current_start

    current_metrics = crm_reports_service.agent_weekly_performance(
        db,
        start_at=current_start,
        end_at=now,
    )
    previous_metrics = crm_reports_service.agent_weekly_performance(
        db,
        start_at=previous_start,
        end_at=previous_end,
    )

    prev_map = {m["agent_id"]: m for m in previous_metrics}

    all_resolved = [m["resolved_count"] for m in current_metrics]
    team_median_resolved = sorted(all_resolved)[len(all_resolved) // 2] if all_resolved else 0

    for m in current_metrics:
        prev = prev_map.get(m["agent_id"], {})
        m["prev_resolved_count"] = prev.get("resolved_count", 0)
        m["prev_median_response_seconds"] = prev.get("median_response_seconds")
        m["prev_median_resolution_seconds"] = prev.get("median_resolution_seconds")
        m["prev_open_backlog"] = prev.get("open_backlog", 0)
        m["prev_csat_avg"] = prev.get("csat_avg")
        m["prev_sla_breach_count"] = prev.get("sla_breach_count", 0)
        m["below_median"] = m["resolved_count"] < team_median_resolved

    return templates.TemplateResponse(
        "admin/reports/agent_performance.html",
        {
            "request": request,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "agent-performance",
            "active_menu": "reports",
            "days": days,
            "agents": current_metrics,
            "team_median_resolved": team_median_resolved,
        },
    )
