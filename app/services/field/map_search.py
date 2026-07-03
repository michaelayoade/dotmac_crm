from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models.person import Person
from app.models.subscriber import Subscriber
from app.models.workforce import WorkOrder
from app.services.field import jobs as field_jobs_service
from app.services.field.location import resolve_job_location
from app.services.field.map_assets import ASSET_CONFIGS, DEFAULT_ASSET_TYPES, _asset_payload
from app.services.workforce import _resolve_site_address


def search_map_places(db: Session, person_id: str | UUID, query: str, *, limit: int = 20) -> list[dict]:
    term = (query or "").strip()
    if not term:
        return []

    results: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in _search_scoped_jobs(db, person_id, term, limit=limit):
        _append_unique(results, seen, item, limit)
    if len(results) < limit:
        for item in _search_assets(db, term, limit=limit - len(results)):
            _append_unique(results, seen, item, limit)
    return results


def _append_unique(results: list[dict], seen: set[tuple[str, str]], item: dict, limit: int) -> None:
    key = (str(item["kind"]), str(item["id"]))
    if key in seen or len(results) >= limit:
        return
    seen.add(key)
    results.append(item)


def _search_scoped_jobs(db: Session, person_id: str | UUID, term: str, *, limit: int) -> list[dict]:
    like_term = f"%{term}%"
    person_uuid = person_id if isinstance(person_id, UUID) else UUID(str(person_id))
    query = (
        field_jobs_service._scoped_query(db, person_uuid)
        .options(joinedload(WorkOrder.subscriber).joinedload(Subscriber.person))
        .outerjoin(Subscriber, WorkOrder.subscriber_id == Subscriber.id)
        .outerjoin(Person, Subscriber.person_id == Person.id)
        .filter(
            or_(
                WorkOrder.title.ilike(like_term),
                WorkOrder.description.ilike(like_term),
                Subscriber.service_address_line1.ilike(like_term),
                Subscriber.service_address_line2.ilike(like_term),
                Subscriber.service_city.ilike(like_term),
                Subscriber.service_region.ilike(like_term),
                Subscriber.account_number.ilike(like_term),
                Person.first_name.ilike(like_term),
                Person.last_name.ilike(like_term),
                Person.email.ilike(like_term),
                Person.phone.ilike(like_term),
                Person.address_line1.ilike(like_term),
                Person.address_line2.ilike(like_term),
                Person.city.ilike(like_term),
                Person.region.ilike(like_term),
            )
        )
        .order_by(WorkOrder.updated_at.desc(), WorkOrder.created_at.desc())
        .limit(limit)
    )
    items = []
    for work_order in query.all():
        location = resolve_job_location(db, work_order)
        latitude = location.get("latitude")
        longitude = location.get("longitude")
        if latitude is None or longitude is None:
            continue
        address_text = location.get("address_text") or _resolve_site_address(work_order)
        items.append(
            {
                "kind": "job",
                "id": work_order.id,
                "title": work_order.title,
                "subtitle": address_text,
                "latitude": float(latitude),
                "longitude": float(longitude),
                "status": work_order.status.value if hasattr(work_order.status, "value") else str(work_order.status),
                "address_text": address_text,
            }
        )
    return items


def _search_assets(db: Session, term: str, *, limit: int) -> list[dict]:
    like_term = f"%{term}%"
    items: list[dict] = []
    for asset_type in DEFAULT_ASSET_TYPES:
        config = ASSET_CONFIGS[asset_type]
        model = config.model
        filters = [getattr(model, config.title_attr).ilike(like_term)]
        for attr in config.subtitle_attrs:
            column = getattr(model, attr, None)
            if column is not None:
                filters.append(column.ilike(like_term))
        query = db.query(model).filter(
            model.latitude.isnot(None),
            model.longitude.isnot(None),
            or_(*filters),
        )
        if hasattr(model, "is_active"):
            query = query.filter(model.is_active.is_(True))
        for row in query.order_by(getattr(model, config.title_attr).asc()).limit(limit).all():
            payload = _asset_payload(asset_type, config, row)
            items.append(
                {
                    "kind": "asset",
                    "id": payload["id"],
                    "asset_type": asset_type,
                    "title": payload["title"],
                    "subtitle": payload["subtitle"],
                    "latitude": payload["latitude"],
                    "longitude": payload["longitude"],
                    "status": payload["status"],
                }
            )
            if len(items) >= limit:
                return items
    return items
