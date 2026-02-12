"""Message template services for CRM inbox."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.crm.enums import ChannelType
from app.models.crm.message_template import CrmMessageTemplate
from app.schemas.crm.message_template import MessageTemplateCreate, MessageTemplateUpdate
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


class MessageTemplates(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: MessageTemplateCreate) -> CrmMessageTemplate:
        template = CrmMessageTemplate(**payload.model_dump())
        db.add(template)
        db.commit()
        db.refresh(template)
        return template

    @staticmethod
    def get(db: Session, template_id: str) -> CrmMessageTemplate:
        template = db.get(CrmMessageTemplate, coerce_uuid(template_id))
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        return template

    @staticmethod
    def update(db: Session, template_id: str, payload: MessageTemplateUpdate) -> CrmMessageTemplate:
        template = MessageTemplates.get(db, template_id)
        data = payload.model_dump(exclude_none=True)
        for key, value in data.items():
            if hasattr(template, key):
                setattr(template, key, value)
        db.commit()
        db.refresh(template)
        return template

    @staticmethod
    def delete(db: Session, template_id: str) -> None:
        template = MessageTemplates.get(db, template_id)
        db.delete(template)
        db.commit()

    @staticmethod
    def list(
        db: Session,
        *,
        channel_type: str | None,
        is_active: bool | None,
        limit: int,
        offset: int,
    ) -> list[CrmMessageTemplate]:
        query = db.query(CrmMessageTemplate)
        if channel_type:
            try:
                channel_enum = ChannelType(channel_type)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid channel type")
            query = query.filter(CrmMessageTemplate.channel_type == channel_enum)
        if is_active is not None:
            query = query.filter(CrmMessageTemplate.is_active == is_active)
        return query.order_by(CrmMessageTemplate.created_at.desc()).offset(offset).limit(limit).all()


message_templates = MessageTemplates()
