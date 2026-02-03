from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.crm.campaign_smtp import CampaignSmtpConfig
from app.schemas.crm.campaign_smtp import CampaignSmtpCreate, CampaignSmtpUpdate
from app.services.common import apply_ordering, apply_pagination, coerce_uuid
from app.services.response import ListResponseMixin


class CampaignSmtpConfigs(ListResponseMixin):
    @staticmethod
    def list(
        db: Session,
        is_active: bool | None = None,
        order_by: str = "name",
        order_dir: str = "asc",
        limit: int = 100,
        offset: int = 0,
    ):
        query = db.query(CampaignSmtpConfig)
        if is_active is not None:
            query = query.filter(CampaignSmtpConfig.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"name": CampaignSmtpConfig.name, "created_at": CampaignSmtpConfig.created_at},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def get(db: Session, smtp_id: str) -> CampaignSmtpConfig:
        smtp = db.get(CampaignSmtpConfig, coerce_uuid(smtp_id))
        if not smtp:
            raise HTTPException(status_code=404, detail="Campaign SMTP profile not found")
        return smtp

    @staticmethod
    def create(db: Session, payload: CampaignSmtpCreate) -> CampaignSmtpConfig:
        smtp = CampaignSmtpConfig(**payload.model_dump())
        db.add(smtp)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="SMTP profile name already exists")
        db.refresh(smtp)
        return smtp

    @staticmethod
    def update(db: Session, smtp_id: str, payload: CampaignSmtpUpdate) -> CampaignSmtpConfig:
        smtp = CampaignSmtpConfigs.get(db, smtp_id)
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(smtp, key, value)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="SMTP profile name already exists")
        db.refresh(smtp)
        return smtp

    @staticmethod
    def deactivate(db: Session, smtp_id: str) -> None:
        smtp = CampaignSmtpConfigs.get(db, smtp_id)
        smtp.is_active = False
        db.commit()


campaign_smtp_configs = CampaignSmtpConfigs()
