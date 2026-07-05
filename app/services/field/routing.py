"""Proximity-aware dispatch helpers: nearest available tech + day routing.

Built on the field-tech presence store (task #42). Distance is computed in
Python (haversine) over the small set of on-shift, sharing-enabled techs, so the
path is DB-agnostic and unit-testable; a PostGIS spatial index can replace it if
the active-tech set ever grows large.

These compose with the existing dispatch scorer rather than replacing it: pass
``candidate_person_ids`` (e.g. the skill-matched, available set from
``app/services/dispatch.py``) to rank only eligible techs by distance.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.field_location import FieldPresenceStatus, FieldTechPresence
from app.models.person import Person
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.services.common import coerce_uuid
from app.services.field.geofence import haversine_m
from app.services.field.location import resolve_job_location
from app.services.field.location_tracking import _now, _person_label

DEFAULT_ASSIGN_STALE_SECONDS = 600  # a tech's fix must be < 10 min old to be "live"
_ROUTABLE_STATUSES = {
    WorkOrderStatus.scheduled,
    WorkOrderStatus.dispatched,
    WorkOrderStatus.in_progress,
    WorkOrderStatus.paused,
}


def _job_coords(db: Session, work_order: WorkOrder) -> tuple[float, float] | None:
    loc = resolve_job_location(db, work_order)
    if loc.get("latitude") is None or loc.get("longitude") is None:
        return None
    return float(loc["latitude"]), float(loc["longitude"])


def nearest_techs_for_job(
    db: Session,
    work_order_id: str,
    *,
    limit: int = 5,
    max_km: float | None = None,
    stale_after_seconds: int = DEFAULT_ASSIGN_STALE_SECONDS,
    candidate_person_ids: list[str] | None = None,
) -> list[dict]:
    """Rank on-shift, sharing-enabled techs by live distance to the job.

    Returns ``[{person_id, person_label, distance_km, last_location_at}]`` nearest
    first. Restrict the pool with ``candidate_person_ids`` to compose with the
    dispatch skill/availability filter.
    """
    work_order = db.get(WorkOrder, coerce_uuid(work_order_id))
    if not work_order:
        raise HTTPException(status_code=404, detail="Work order not found")
    coords = _job_coords(db, work_order)
    if coords is None:
        return []
    job_lat, job_lng = coords

    cutoff = _now() - timedelta(seconds=max(int(stale_after_seconds or DEFAULT_ASSIGN_STALE_SECONDS), 30))
    query = (
        db.query(FieldTechPresence, Person)
        .join(Person, Person.id == FieldTechPresence.person_id)
        .filter(FieldTechPresence.location_sharing_enabled.is_(True))
        .filter(FieldTechPresence.status == FieldPresenceStatus.on_shift)
        .filter(FieldTechPresence.last_location_at.isnot(None))
        .filter(FieldTechPresence.last_location_at >= cutoff)
    )
    if candidate_person_ids is not None:
        wanted = [coerce_uuid(pid) for pid in candidate_person_ids]
        if not wanted:
            return []
        query = query.filter(FieldTechPresence.person_id.in_(wanted))

    ranked: list[dict] = []
    for presence, person in query.all():
        distance_km = haversine_m(job_lat, job_lng, presence.last_latitude, presence.last_longitude) / 1000.0
        if max_km is not None and distance_km > max_km:
            continue
        ranked.append(
            {
                "person_id": str(presence.person_id),
                "person_label": _person_label(person),
                "distance_km": round(distance_km, 3),
                "last_location_at": presence.last_location_at,
            }
        )
    ranked.sort(key=lambda r: r["distance_km"])
    return ranked[: max(1, int(limit or 5))]


def suggest_nearest_tech(
    db: Session,
    work_order_id: str,
    *,
    max_km: float | None = None,
    candidate_person_ids: list[str] | None = None,
) -> dict | None:
    """The single nearest eligible tech, or None if no live candidate is in range."""
    ranked = nearest_techs_for_job(
        db,
        work_order_id,
        limit=1,
        max_km=max_km,
        candidate_person_ids=candidate_person_ids,
    )
    return ranked[0] if ranked else None


def order_day_route(
    db: Session,
    person_id: str,
    *,
    start_latitude: float,
    start_longitude: float,
) -> list[dict]:
    """Greedy nearest-neighbour ordering of a tech's open jobs from a start point.

    Jobs without a resolvable location sort last (in their original order) so the
    route never silently drops work. Returns
    ``[{sequence, work_order_id, distance_km, leg_km}]``.
    """
    person_uuid = coerce_uuid(person_id)
    jobs = (
        db.query(WorkOrder)
        .filter(WorkOrder.assigned_to_person_id == person_uuid)
        .filter(WorkOrder.status.in_(_ROUTABLE_STATUSES))
        .all()
    )
    located: list[tuple[WorkOrder, float, float]] = []
    unlocated: list[WorkOrder] = []
    for job in jobs:
        coords = _job_coords(db, job)
        if coords is None:
            unlocated.append(job)
        else:
            located.append((job, coords[0], coords[1]))

    route: list[dict] = []
    cur_lat, cur_lng = float(start_latitude), float(start_longitude)
    total_km = 0.0
    seq = 0
    remaining = located[:]
    while remaining:
        best_i = min(
            range(len(remaining)),
            key=lambda i: haversine_m(cur_lat, cur_lng, remaining[i][1], remaining[i][2]),
        )
        job, jlat, jlng = remaining.pop(best_i)
        leg_km = haversine_m(cur_lat, cur_lng, jlat, jlng) / 1000.0
        total_km += leg_km
        seq += 1
        route.append(
            {
                "sequence": seq,
                "work_order_id": str(job.id),
                "distance_km": round(total_km, 3),
                "leg_km": round(leg_km, 3),
            }
        )
        cur_lat, cur_lng = jlat, jlng

    for job in unlocated:
        seq += 1
        route.append(
            {
                "sequence": seq,
                "work_order_id": str(job.id),
                "distance_km": None,
                "leg_km": None,
            }
        )
    return route
