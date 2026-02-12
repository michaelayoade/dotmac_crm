from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.crm.conversation import MessageCreate, MessageRead, MessageUpdate
from app.services import crm as crm_service

router = APIRouter(prefix="/crm/messages", tags=["crm-messages"])


@router.post("", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
def create_message(payload: MessageCreate, db: Session = Depends(get_db)):
    return crm_service.messages.create(db, payload)


@router.get("/{message_id}", response_model=MessageRead)
def get_message(message_id: str, db: Session = Depends(get_db)):
    return crm_service.messages.get(db, message_id)


@router.get("", response_model=ListResponse[MessageRead])
def list_messages(
    conversation_id: str | None = None,
    channel_type: str | None = None,
    direction: str | None = None,
    status: str | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return crm_service.messages.list_response(
        db,
        conversation_id,
        channel_type,
        direction,
        status,
        order_by,
        order_dir,
        limit,
        offset,
    )


@router.patch("/{message_id}", response_model=MessageRead)
def update_message(message_id: str, payload: MessageUpdate, db: Session = Depends(get_db)):
    return crm_service.messages.update(db, message_id, payload)
