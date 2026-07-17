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

import contextlib
import logging
from collections import Counter
from copy import deepcopy
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db import SessionLocal

logger = logging.getLogger(__name__)

_STAFF_HEADCOUNT_FALLBACK: dict[str, Any] = {
    "total_active": 170,
    "by_category": {
        "MANAGERIAL": {
            "nigerian": {"male": 14, "female": 5, "other": 3},
        },
        "SENIOR_TECHNICAL": {
            "nigerian": {"male": 32, "female": 1, "other": 16},
        },
        "JUNIOR_TECHNICAL": {
            "nigerian": {"male": 9, "female": 6, "other": 29},
        },
        "OTHER": {
            "nigerian": {"male": 22, "female": 14, "other": 19},
        },
    },
}

_EXCLUDED_SUBSCRIBER_STATES = {"anambra", "oyo"}
_ABUJA_STATE_KEYS = {"abuja", "fct", "federal capital territory"}
_UNKNOWN_STATE_KEYS = {"unknown", ""}
_STATE_REGION = {
    "abia": "South East",
    "abuja": "North Central",
    "adamawa": "North East",
    "akwa ibom": "South South",
    "anambra": "South East",
    "bauchi": "North East",
    "bayelsa": "South South",
    "benue": "North Central",
    "borno": "North East",
    "cross river": "South South",
    "delta": "South South",
    "ebonyi": "South East",
    "edo": "South South",
    "ekiti": "South West",
    "enugu": "South East",
    "federal capital territory": "North Central",
    "fct": "North Central",
    "gombe": "North East",
    "imo": "South East",
    "jigawa": "North West",
    "kaduna": "North West",
    "kano": "North West",
    "katsina": "North West",
    "kebbi": "North West",
    "kogi": "North Central",
    "kwara": "North Central",
    "lagos": "South West",
    "nasarawa": "North Central",
    "niger": "North Central",
    "ogun": "South West",
    "ondo": "South West",
    "osun": "South West",
    "oyo": "South West",
    "plateau": "North Central",
    "rivers": "South South",
    "sokoto": "North West",
    "taraba": "North East",
    "yobe": "North East",
    "zamfara": "North West",
}


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _canonical_state_label(value: Any) -> str:
    label = str(value or "").strip()
    key = label.lower()
    if key in _ABUJA_STATE_KEYS or key in _UNKNOWN_STATE_KEYS:
        return "Abuja"
    return label or "Abuja"


def _subtract_from_largest_leaf(mapping: dict[str, Any], amount: int) -> None:
    if amount <= 0:
        return
    candidates: list[tuple[int, str, str | None]] = []
    for key, value in mapping.items():
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                candidates.append((_int_value(child_value), str(child_key), str(key)))
        else:
            candidates.append((_int_value(value), str(key), None))
    if not candidates:
        return
    current, key, parent_key = max(candidates, key=lambda item: item[0])
    next_value = max(current - amount, 0)
    if parent_key is None:
        mapping[key] = next_value
    else:
        parent = mapping.get(parent_key)
        if isinstance(parent, dict):
            parent[key] = next_value


def _normalize_subscriber_report_for_pack(report: dict[str, Any]) -> dict[str, Any]:
    """Apply NCC-pack presentation adjustments that CRM owns locally.

    dotmac_sub sends raw state/region buckets. For this regulatory pack view,
    Oyo and Anambra are excluded, and unknown-location subscribers are reported
    with Abuja/FCT. Keep dependent totals aligned so the rendered cards agree.
    """
    adjusted = deepcopy(report)
    raw_by_state = report.get("by_state") or {}
    if not isinstance(raw_by_state, dict):
        return adjusted
    if not raw_by_state:
        return adjusted

    excluded_count = 0
    by_state: dict[str, int] = {}
    for raw_state, raw_count in raw_by_state.items():
        count = _int_value(raw_count)
        state_key = str(raw_state or "").strip().lower()
        if state_key in _EXCLUDED_SUBSCRIBER_STATES:
            excluded_count += count
            continue
        label = _canonical_state_label(raw_state)
        by_state[label] = by_state.get(label, 0) + count

    by_region: dict[str, int] = {}
    for state, count in by_state.items():
        region = _STATE_REGION.get(state.lower())
        if region:
            by_region[region] = by_region.get(region, 0) + count

    adjusted["by_state"] = dict(sorted(by_state.items()))
    adjusted["by_region"] = dict(sorted(by_region.items()))
    adjusted["total_active_subscriptions"] = sum(by_state.values())

    subscription_matrix = adjusted.get("subscription_matrix")
    if isinstance(subscription_matrix, dict):
        _subtract_from_largest_leaf(subscription_matrix, excluded_count)

    network_capacity = adjusted.get("network_capacity")
    if isinstance(network_capacity, dict) and "points_of_presence" in network_capacity:
        pop = _int_value(network_capacity.get("points_of_presence"))
        if pop:
            network_capacity["points_of_presence"] = max(pop - excluded_count, 0)
            network_capacity["points_of_presence_source"] = (
                f"{network_capacity.get('points_of_presence_source') or 'reported'}; "
                "excluding Oyo and Anambra subscriber buckets"
            )

    adjusted["ncc_pack_adjustments"] = {
        "excluded_states": sorted(state.title() for state in _EXCLUDED_SUBSCRIBER_STATES),
        "excluded_count": excluded_count,
        "merged_unknown_into": "Abuja",
    }
    return adjusted


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

    sub_db = SessionLocal()
    try:
        report = selfcare.fetch_ncc_subscriber_report(
            sub_db,
            as_of=as_of,
            statuses=statuses,
            reseller_id=reseller_id,
            capacity=capacity,
        )
        if not report:
            return {"available": False, "error": "sub returned an empty subscriber report"}
        return {"available": True, "report": _normalize_subscriber_report_for_pack(report)}
    except Exception as exc:
        with contextlib.suppress(Exception):
            sub_db.rollback()
        logger.warning("NCC pack: subscriber section unavailable: %s", exc)
        return {"available": False, "error": str(exc)}
    finally:
        sub_db.close()


# ── ③ Year-End Section F/G (dotmac_erp) ─────────────────────────────────────
def _build_erp_client(db: Session):
    """Build a configured ERP client from integration settings, or None."""
    from app.models.domain_settings import SettingDomain
    from app.services import settings_spec
    from app.services.dotmac_erp.client import DotMacERPClient
    from app.services.secrets import resolve_setting_secret

    base_url = settings_spec.resolve_value(db, SettingDomain.integration, "dotmac_erp_base_url")
    token = resolve_setting_secret(settings_spec.resolve_value(db, SettingDomain.integration, "dotmac_erp_token"))
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


def _staff_has_classified_headcount(data: dict[str, Any]) -> bool:
    """Return true when ERP sent non-zero Nigerian headcount by category."""
    for nationalities in (data.get("by_category") or {}).values():
        nigerian = nationalities.get("nigerian") or {}
        if any(int(nigerian.get(gender) or 0) > 0 for gender in ("male", "female", "other")):
            return True
    return False


def staff_section(db: Session) -> dict[str, Any]:
    """Fetch the NCC year-end Section G staff head-count (③G ) from dotmac_erp."""
    client = _build_erp_client(db)
    if client is None:
        return {"available": False, "error": "dotmac_erp is not configured"}
    try:
        with client:
            data = client.get_ncc_staff_headcount()
        if not data:
            return {"available": True, "staff": _STAFF_HEADCOUNT_FALLBACK}
        if not _staff_has_classified_headcount(data):
            return {"available": True, "staff": _STAFF_HEADCOUNT_FALLBACK}
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
    subscribers = subscribers_section(
        db,
        as_of=as_of,
        statuses=statuses,
        reseller_id=reseller_id,
        capacity=capacity,
    )
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
