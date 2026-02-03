from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.crm.campaign_sender import CampaignSender
from app.schemas.crm.campaign_sender import CampaignSenderCreate, CampaignSenderUpdate
from app.services.common import apply_ordering, apply_pagination, coerce_uuid
from app.services.response import ListResponseMixin


class CampaignSenders(ListResponseMixin):
    @staticmethod
    def list(
        db: Session,
        is_active: bool | None = None,
        order_by: str = "name",
        order_dir: str = "asc",
        limit: int = 100,
        offset: int = 0,
    ):
        query = db.query(CampaignSender)
        if is_active is not None:
            query = query.filter(CampaignSender.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"name": CampaignSender.name, "created_at": CampaignSender.created_at},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def get(db: Session, sender_id: str) -> CampaignSender:
        sender = db.get(CampaignSender, coerce_uuid(sender_id))
        if not sender:
            raise HTTPException(status_code=404, detail="Campaign sender not found")
        return sender

    @staticmethod
    def create(db: Session, payload: CampaignSenderCreate) -> CampaignSender:
        sender = CampaignSender(**payload.model_dump())
        db.add(sender)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Sender email already exists")
        db.refresh(sender)
        return sender

    @staticmethod
    def update(db: Session, sender_id: str, payload: CampaignSenderUpdate) -> CampaignSender:
        sender = CampaignSenders.get(db, sender_id)
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(sender, key, value)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Sender email already exists")
        db.refresh(sender)
        return sender

    @staticmethod
    def deactivate(db: Session, sender_id: str) -> None:
        sender = CampaignSenders.get(db, sender_id)
        sender.is_active = False
        db.commit()


campaign_senders = CampaignSenders()
