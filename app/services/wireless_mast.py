from geoalchemy2.functions import ST_MakePoint, ST_SetSRID
from sqlalchemy.orm import Session

from app.models.wireless_mast import WirelessMast
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, validate_enum
from app.schemas.wireless_mast import WirelessMastCreate, WirelessMastUpdate
from app.services.response import ListResponseMixin
from fastapi import HTTPException


class WirelessMasts(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: WirelessMastCreate) -> WirelessMast:
        mast = WirelessMast(**payload.model_dump())
        mast.geom = ST_SetSRID(ST_MakePoint(payload.longitude, payload.latitude), 4326)
        db.add(mast)
        db.commit()
        db.refresh(mast)
        return mast

    @staticmethod
    def get(db: Session, mast_id: str) -> WirelessMast:
        mast = db.get(WirelessMast, mast_id)
        if not mast:
            raise HTTPException(status_code=404, detail="Wireless mast not found")
        return mast

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None,
        min_latitude: float | None,
        min_longitude: float | None,
        max_latitude: float | None,
        max_longitude: float | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> list[WirelessMast]:
        query = db.query(WirelessMast)
        if is_active is None:
            query = query.filter(WirelessMast.is_active.is_(True))
        else:
            query = query.filter(WirelessMast.is_active == is_active)
        if None not in (min_latitude, min_longitude, max_latitude, max_longitude):
            query = query.filter(WirelessMast.latitude >= min_latitude)
            query = query.filter(WirelessMast.longitude >= min_longitude)
            query = query.filter(WirelessMast.latitude <= max_latitude)
            query = query.filter(WirelessMast.longitude <= max_longitude)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": WirelessMast.created_at, "name": WirelessMast.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, mast_id: str, payload: WirelessMastUpdate) -> WirelessMast:
        mast = WirelessMasts.get(db, mast_id)
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(mast, key, value)
        if "latitude" in data or "longitude" in data:
            lat = data.get("latitude", mast.latitude)
            lon = data.get("longitude", mast.longitude)
            mast.geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326)
        db.commit()
        db.refresh(mast)
        return mast

    @staticmethod
    def delete(db: Session, mast_id: str) -> None:
        mast = WirelessMasts.get(db, mast_id)
        mast.is_active = False
        db.commit()


wireless_masts = WirelessMasts()
