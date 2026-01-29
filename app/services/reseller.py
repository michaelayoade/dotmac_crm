"""Reseller service."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.subscriber import Reseller
from app.schemas.subscriber import ResellerCreate, ResellerUpdate
from app.services.common import apply_ordering, apply_pagination, coerce_uuid
from app.services.response import ListResponseMixin


class Resellers(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ResellerCreate):
        reseller = Reseller(**payload.model_dump())
        db.add(reseller)
        db.commit()
        db.refresh(reseller)
        return reseller

    @staticmethod
    def get(db: Session, reseller_id: str):
        reseller = db.get(Reseller, coerce_uuid(reseller_id))
        if not reseller:
            raise HTTPException(status_code=404, detail="Reseller not found")
        return reseller

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(Reseller)
        if is_active is not None:
            query = query.filter(Reseller.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": Reseller.created_at, "name": Reseller.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, reseller_id: str, payload: ResellerUpdate):
        reseller = db.get(Reseller, coerce_uuid(reseller_id))
        if not reseller:
            raise HTTPException(status_code=404, detail="Reseller not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(reseller, key, value)
        db.commit()
        db.refresh(reseller)
        return reseller

    @staticmethod
    def delete(db: Session, reseller_id: str):
        reseller = db.get(Reseller, coerce_uuid(reseller_id))
        if not reseller:
            raise HTTPException(status_code=404, detail="Reseller not found")
        reseller.is_active = False
        db.commit()


resellers = Resellers()
