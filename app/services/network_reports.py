"""Network infrastructure report service functions."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.network import (
    FdhCabinet,
    FiberSegment,
    FiberStrand,
    FiberStrandStatus,
    OLTDevice,
    OntAssignment,
    OntUnit,
    PonPort,
    Splitter,
    SplitterPort,
)


def get_network_kpis(db: Session) -> dict:
    """Core network KPIs: OLT count, ONT count, PON util, fiber health, total km."""
    active_olts = db.scalar(select(func.count(OLTDevice.id)).where(OLTDevice.is_active.is_(True))) or 0

    connected_onts = db.scalar(select(func.count(OntAssignment.id)).where(OntAssignment.active.is_(True))) or 0

    # PON port utilization
    total_pon = db.scalar(select(func.count(PonPort.id)).where(PonPort.is_active.is_(True))) or 0
    assigned_pon = (
        db.scalar(select(func.count(func.distinct(OntAssignment.pon_port_id))).where(OntAssignment.active.is_(True)))
        or 0
    )
    pon_util = (assigned_pon / total_pon * 100) if total_pon > 0 else 0

    # Fiber strand health: % available or in_use
    total_strands = db.scalar(select(func.count(FiberStrand.id)).where(FiberStrand.is_active.is_(True))) or 0
    healthy_strands = (
        db.scalar(
            select(func.count(FiberStrand.id)).where(
                FiberStrand.is_active.is_(True),
                FiberStrand.status.in_([FiberStrandStatus.available, FiberStrandStatus.in_use]),
            )
        )
        or 0
    )
    fiber_health = (healthy_strands / total_strands * 100) if total_strands > 0 else 0

    # Total fiber deployed (km)
    total_fiber_m = (
        db.scalar(select(func.coalesce(func.sum(FiberSegment.length_m), 0)).where(FiberSegment.is_active.is_(True)))
        or 0
    )
    total_fiber_km = round(float(total_fiber_m) / 1000, 1)

    return {
        "active_olts": active_olts,
        "connected_onts": connected_onts,
        "pon_util_pct": round(pon_util, 1),
        "total_pon_ports": total_pon,
        "assigned_pon_ports": assigned_pon,
        "fiber_health_pct": round(fiber_health, 1),
        "total_fiber_km": total_fiber_km,
    }


def get_olt_capacity(db: Session) -> list[dict]:
    """Per-OLT: total PON ports vs assigned (for bar chart)."""
    olts = db.scalars(select(OLTDevice).where(OLTDevice.is_active.is_(True)).order_by(OLTDevice.name)).all()

    olt_ids = [o.id for o in olts]
    if not olt_ids:
        return []

    # Total PON ports per OLT
    total_rows = db.execute(
        select(PonPort.olt_id, func.count(PonPort.id))
        .where(PonPort.is_active.is_(True), PonPort.olt_id.in_(olt_ids))
        .group_by(PonPort.olt_id)
    ).all()
    total_map = {row[0]: row[1] for row in total_rows}

    # Assigned PON ports per OLT (has at least one active ONT assignment)
    assigned_rows = db.execute(
        select(PonPort.olt_id, func.count(func.distinct(OntAssignment.pon_port_id)))
        .join(OntAssignment, OntAssignment.pon_port_id == PonPort.id)
        .where(
            PonPort.is_active.is_(True),
            PonPort.olt_id.in_(olt_ids),
            OntAssignment.active.is_(True),
        )
        .group_by(PonPort.olt_id)
    ).all()
    assigned_map = {row[0]: row[1] for row in assigned_rows}

    return [
        {
            "name": olt.name or str(olt.id)[:8],
            "total_ports": total_map.get(olt.id, 0),
            "assigned_ports": assigned_map.get(olt.id, 0),
        }
        for olt in olts
    ]


def get_fiber_strand_status(db: Session) -> dict[str, int]:
    """Fiber strand counts by status (for doughnut chart)."""
    rows = db.execute(
        select(FiberStrand.status, func.count(FiberStrand.id))
        .where(FiberStrand.is_active.is_(True))
        .group_by(FiberStrand.status)
    ).all()
    return {(status.value if status else "unknown"): count for status, count in rows}


def get_ont_activation_trend(db: Session, start_dt: datetime, end_dt: datetime) -> list[dict]:
    """Daily ONT assignment count in range (for line chart)."""
    rows = db.execute(
        select(
            func.date_trunc("day", OntAssignment.assigned_at).label("day"),
            func.count(OntAssignment.id),
        )
        .where(
            OntAssignment.assigned_at >= start_dt,
            OntAssignment.assigned_at <= end_dt,
        )
        .group_by("day")
        .order_by("day")
    ).all()
    return [{"date": row[0].strftime("%Y-%m-%d"), "count": row[1]} for row in rows if row[0]]


def get_olt_table(db: Session) -> list[dict]:
    """OLT status table with PON port and ONT counts."""
    olts = db.scalars(select(OLTDevice).where(OLTDevice.is_active.is_(True)).order_by(OLTDevice.name)).all()
    if not olts:
        return []

    olt_ids = [o.id for o in olts]

    # PON ports per OLT
    pon_rows = db.execute(
        select(PonPort.olt_id, func.count(PonPort.id))
        .where(PonPort.is_active.is_(True), PonPort.olt_id.in_(olt_ids))
        .group_by(PonPort.olt_id)
    ).all()
    pon_map = {row[0]: row[1] for row in pon_rows}

    # ONTs per OLT (via PonPort)
    ont_rows = db.execute(
        select(PonPort.olt_id, func.count(OntAssignment.id))
        .join(OntAssignment, OntAssignment.pon_port_id == PonPort.id)
        .where(
            PonPort.olt_id.in_(olt_ids),
            OntAssignment.active.is_(True),
        )
        .group_by(PonPort.olt_id)
    ).all()
    ont_map = {row[0]: row[1] for row in ont_rows}

    # Assigned PON ports per OLT
    used_pon_rows = db.execute(
        select(PonPort.olt_id, func.count(func.distinct(OntAssignment.pon_port_id)))
        .join(OntAssignment, OntAssignment.pon_port_id == PonPort.id)
        .where(
            PonPort.olt_id.in_(olt_ids),
            OntAssignment.active.is_(True),
        )
        .group_by(PonPort.olt_id)
    ).all()
    used_pon_map = {row[0]: row[1] for row in used_pon_rows}

    return [
        {
            "name": olt.name,
            "hostname": olt.hostname,
            "mgmt_ip": olt.mgmt_ip,
            "vendor": olt.vendor,
            "model": olt.model,
            "pon_used": used_pon_map.get(olt.id, 0),
            "pon_total": pon_map.get(olt.id, 0),
            "ont_count": ont_map.get(olt.id, 0),
            "site_role": olt.site_role.value if olt.site_role else "olt",
        }
        for olt in olts
    ]


def get_fdh_utilization(db: Session) -> list[dict]:
    """FDH cabinet utilization table."""
    fdhs = db.scalars(select(FdhCabinet).where(FdhCabinet.is_active.is_(True)).order_by(FdhCabinet.name)).all()
    if not fdhs:
        return []

    fdh_ids = [f.id for f in fdhs]

    # Splitter count per FDH
    splitter_rows = db.execute(
        select(Splitter.fdh_id, func.count(Splitter.id))
        .where(Splitter.is_active.is_(True), Splitter.fdh_id.in_(fdh_ids))
        .group_by(Splitter.fdh_id)
    ).all()
    splitter_map = {row[0]: row[1] for row in splitter_rows}

    # Total and used splitter ports per FDH
    splitter_ids_by_fdh: dict = {}
    splitters = db.execute(
        select(Splitter.id, Splitter.fdh_id).where(Splitter.is_active.is_(True), Splitter.fdh_id.in_(fdh_ids))
    ).all()
    for sid, fid in splitters:
        splitter_ids_by_fdh.setdefault(fid, []).append(sid)

    all_splitter_ids = [s[0] for s in splitters]
    total_port_rows = (
        db.execute(
            select(SplitterPort.splitter_id, func.count(SplitterPort.id))
            .where(
                SplitterPort.is_active.is_(True),
                SplitterPort.splitter_id.in_(all_splitter_ids) if all_splitter_ids else False,
            )
            .group_by(SplitterPort.splitter_id)
        ).all()
        if all_splitter_ids
        else []
    )
    total_ports_map = {row[0]: row[1] for row in total_port_rows}

    results = []
    for fdh in fdhs:
        sids = splitter_ids_by_fdh.get(fdh.id, [])
        total_ports = sum(total_ports_map.get(sid, 0) for sid in sids)
        results.append(
            {
                "name": fdh.name,
                "code": fdh.code,
                "splitter_count": splitter_map.get(fdh.id, 0),
                "total_ports": total_ports,
                "ports_used": 0,  # Would need FiberStrand upstream tracking
                "util_pct": 0,
            }
        )

    return results


def get_fiber_inventory(db: Session) -> list[dict]:
    """Fiber inventory by segment type."""
    rows = db.execute(
        select(
            FiberSegment.segment_type,
            func.count(FiberSegment.id),
            func.coalesce(func.sum(FiberSegment.length_m), 0),
            func.coalesce(func.sum(FiberSegment.fiber_count), 0),
        )
        .where(FiberSegment.is_active.is_(True))
        .group_by(FiberSegment.segment_type)
    ).all()

    return [
        {
            "segment_type": (row[0].value if row[0] else "unknown"),
            "count": row[1],
            "total_km": round(float(row[2]) / 1000, 2),
            "fiber_count": row[3],
        }
        for row in rows
    ]


def get_recent_ont_activity(db: Session, limit: int = 10) -> list[dict]:
    """Recent ONT assignments with customer and OLT info."""
    assignments = db.execute(
        select(
            OntAssignment.assigned_at,
            OntUnit.serial_number,
            OntUnit.model,
            OLTDevice.name.label("olt_name"),
        )
        .join(OntUnit, OntUnit.id == OntAssignment.ont_unit_id)
        .join(PonPort, PonPort.id == OntAssignment.pon_port_id)
        .join(OLTDevice, OLTDevice.id == PonPort.olt_id)
        .order_by(OntAssignment.assigned_at.desc())
        .limit(limit)
    ).all()

    results = []
    for row in assignments:
        results.append(
            {
                "serial_number": row.serial_number,
                "model": row.model or "Unknown",
                "olt_name": row.olt_name,
                "assigned_at": row.assigned_at.strftime("%Y-%m-%d %H:%M") if row.assigned_at else "",
            }
        )

    return results


def get_network_export_data(db: Session) -> list[dict]:
    """Export data combining OLT status and fiber inventory."""
    olt_data = get_olt_table(db)
    export = []
    for olt in olt_data:
        export.append(
            {
                "Type": "OLT",
                "Name": olt["name"],
                "Hostname": olt["hostname"],
                "Management IP": olt["mgmt_ip"],
                "Vendor": olt["vendor"],
                "Model": olt["model"],
                "PON Ports Used": olt["pon_used"],
                "PON Ports Total": olt["pon_total"],
                "ONTs Connected": olt["ont_count"],
                "Role": olt["site_role"],
            }
        )

    fiber_data = get_fiber_inventory(db)
    for seg in fiber_data:
        export.append(
            {
                "Type": f"Fiber ({seg['segment_type']})",
                "Name": f"{seg['count']} segments",
                "Hostname": "",
                "Management IP": "",
                "Vendor": "",
                "Model": "",
                "PON Ports Used": "",
                "PON Ports Total": "",
                "ONTs Connected": seg["fiber_count"],
                "Role": f"{seg['total_km']} km",
            }
        )

    return export
