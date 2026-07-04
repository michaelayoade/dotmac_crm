"""NCC regulatory-pack aggregator.

Assembles the three NCC (Nigerian Communications Commission) returns from the
three DotMac systems into one payload, so a compliance officer produces the
filing from a single view instead of stitching three tools together:

  ① Quarterly Complaints        — native (CRM tickets, ``_build_ncc_records``)
  ② Quarterly Subscriber/Capacity — dotmac_sub via the ``/crm`` bearer API
  ③ Annual Year-End Section F/G  — dotmac_erp via the ``/sync/crm`` service API

The CRM is the system-of-record and owns ① natively; ② and ③ are fetched from
their owning systems. Those external sections degrade gracefully — if sub or
erp is unreachable or not configured, that section carries
``{"available": False, "error": ...}`` rather than failing the whole pack, so
the ① native return always renders and the officer can see exactly which
upstream is missing.

Only the annual return's narrative pages (③'s free-text) stay manual.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ── ① Complaints (native CRM) ───────────────────────────────────────────────
def complaints_section(db: Session, start_dt: datetime, end_dt: datetime) -> dict[str, Any]:
    """Summarise the NCC quarterly complaints return (① ) from CRM tickets.

    Returns a rollup (total + by category / status / SLA) over the same records
    the ``/admin/reports`` NCC export emits, so the pack and the filed workbook
    agree. Always available — this data is native to the CRM.
    """
    from app.web.admin.reports import _build_ncc_records

    records = _build_ncc_records(db, start_dt, end_dt)
    by_category: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    resolved_within_sla = 0
    resolved_total = 0
    for row in records:
        by_category[row.get("Category") or "Unknown"] += 1
        status = row.get("Status") or "Unknown"
        by_status[status] += 1
        sla = (row.get("Resolved within SLA") or "").strip().lower()
        if sla in {"yes", "no"}:
            resolved_total += 1
            if sla == "yes":
                resolved_within_sla += 1

    return {
        "available": True,
        "total_complaints": len(records),
        "by_category": dict(sorted(by_category.items())),
        "by_status": dict(sorted(by_status.items())),
        "resolved_within_sla": resolved_within_sla,
        "resolved_total": resolved_total,
        "period": {"start": start_dt.isoformat(), "end": end_dt.isoformat()},
    }


# ── ② Subscribers & Capacity (dotmac_sub) ───────────────────────────────────
def subscribers_section(
    db: Session,
    *,
    as_of: str | None = None,
    statuses: str | None = None,
    reseller_id: str | None = None,
    capacity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch the NCC subscriber/capacity aggregate (② ) from dotmac_sub."""
    from app.services import selfcare

    try:
        report = selfcare.fetch_ncc_subscriber_report(
            db,
            as_of=as_of,
            statuses=statuses,
            reseller_id=reseller_id,
            capacity=capacity,
        )
        if not report:
            return {"available": False, "error": "sub returned an empty subscriber report"}
        return {"available": True, "report": report}
    except Exception as exc:
        logger.warning("NCC pack: subscriber section unavailable: %s", exc)
        return {"available": False, "error": str(exc)}


# ── ③ Year-End Section F/G (dotmac_erp) ─────────────────────────────────────
def _build_erp_client(db: Session):
    """Build a configured ERP client from integration settings, or None."""
    from app.models.domain_settings import SettingDomain
    from app.services import settings_spec
    from app.services.dotmac_erp.client import DotMacERPClient

    base_url = settings_spec.resolve_value(db, SettingDomain.integration, "dotmac_erp_base_url")
    token = settings_spec.resolve_value(db, SettingDomain.integration, "dotmac_erp_token")
    if not base_url or not token:
        return None
    return DotMacERPClient(base_url=str(base_url), token=str(token))


def financials_section(db: Session, *, year: int | None = None) -> dict[str, Any]:
    """Fetch the NCC year-end Section F financials (③F ) from dotmac_erp."""
    client = _build_erp_client(db)
    if client is None:
        return {"available": False, "error": "dotmac_erp is not configured"}
    try:
        with client:
            data = client.get_ncc_financials(year=year)
        if not data:
            return {"available": False, "error": "erp returned empty financials"}
        return {"available": True, "financials": data}
    except Exception as exc:
        logger.warning("NCC pack: financials section unavailable: %s", exc)
        return {"available": False, "error": str(exc)}


def staff_section(db: Session) -> dict[str, Any]:
    """Fetch the NCC year-end Section G staff head-count (③G ) from dotmac_erp."""
    client = _build_erp_client(db)
    if client is None:
        return {"available": False, "error": "dotmac_erp is not configured"}
    try:
        with client:
            data = client.get_ncc_staff_headcount()
        if not data:
            return {"available": False, "error": "erp returned empty staff head-count"}
        return {"available": True, "staff": data}
    except Exception as exc:
        logger.warning("NCC pack: staff section unavailable: %s", exc)
        return {"available": False, "error": str(exc)}


# ── The pack ────────────────────────────────────────────────────────────────
def build_regulatory_pack(
    db: Session,
    *,
    start_dt: datetime,
    end_dt: datetime,
    as_of: str | None = None,
    year: int | None = None,
    statuses: str | None = None,
    reseller_id: str | None = None,
    capacity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the full NCC regulatory pack from all three systems.

    ``start_dt``/``end_dt`` bound the quarterly complaints return; ``as_of`` is
    the subscriber period-end; ``year`` selects the annual financials/staff
    year. External sections degrade gracefully so the pack always returns.
    """
    complaints = complaints_section(db, start_dt, end_dt)
    subscribers = subscribers_section(db, as_of=as_of, statuses=statuses, reseller_id=reseller_id, capacity=capacity)
    financials = financials_section(db, year=year)
    staff = staff_section(db)

    sources = {
        "complaints": complaints.get("available", False),
        "subscribers": subscribers.get("available", False),
        "financials": financials.get("available", False),
        "staff": staff.get("available", False),
    }
    return {
        "meta": {
            "period": {"start": start_dt.isoformat(), "end": end_dt.isoformat()},
            "as_of": as_of,
            "year": year,
            "sources": sources,
            "complete": all(sources.values()),
        },
        # ① Quarterly complaints
        "complaints": complaints,
        # ② Quarterly subscriber & capacity
        "subscribers": subscribers,
        # ③ Annual year-end (financials + staff; narrative pages remain manual)
        "financials": financials,
        "staff": staff,
    }
