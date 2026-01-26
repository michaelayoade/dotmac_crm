from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.schemas.common import ListResponse
from app.schemas.crm.conversation import (
    ConversationAssignmentCreate,
    ConversationAssignmentRead,
    ConversationCreate,
    ConversationRead,
    ConversationTagCreate,
    ConversationTagRead,
    ConversationUpdate,
)
from app.services import crm as crm_service

router = APIRouter(prefix="/crm/conversations", tags=["crm-conversations"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
def create_conversation(payload: ConversationCreate, db: Session = Depends(get_db)):
    return crm_service.conversations.create(db, payload)


@router.get("/{conversation_id}", response_model=ConversationRead)
def get_conversation(conversation_id: str, db: Session = Depends(get_db)):
    return crm_service.conversations.get(db, conversation_id)


@router.get("", response_model=ListResponse[ConversationRead])
def list_conversations(
    person_id: str | None = None,
    ticket_id: str | None = None,
    status: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="last_message_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return crm_service.conversations.list_response(
        db,
        person_id,
        ticket_id,
        status,
        is_active,
        order_by,
        order_dir,
        limit,
        offset,
    )


@router.patch("/{conversation_id}", response_model=ConversationRead)
def update_conversation(
    conversation_id: str, payload: ConversationUpdate, db: Session = Depends(get_db)
):
    return crm_service.conversations.update(db, conversation_id, payload)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: str, db: Session = Depends(get_db)):
    crm_service.conversations.delete(db, conversation_id)


@router.post(
    "/{conversation_id}/assignments",
    response_model=ConversationAssignmentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_assignment(
    conversation_id: str,
    payload: ConversationAssignmentCreate,
    db: Session = Depends(get_db),
):
    data = payload.model_copy(update={"conversation_id": conversation_id})
    return crm_service.conversation_assignments.create(db, data)


@router.get(
    "/{conversation_id}/assignments",
    response_model=ListResponse[ConversationAssignmentRead],
)
def list_assignments(
    conversation_id: str,
    team_id: str | None = None,
    agent_id: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return crm_service.conversation_assignments.list_response(
        db,
        conversation_id,
        team_id,
        agent_id,
        is_active,
        order_by,
        order_dir,
        limit,
        offset,
    )


@router.post(
    "/{conversation_id}/tags",
    response_model=ConversationTagRead,
    status_code=status.HTTP_201_CREATED,
)
def create_tag(
    conversation_id: str,
    payload: ConversationTagCreate,
    db: Session = Depends(get_db),
):
    data = payload.model_copy(update={"conversation_id": conversation_id})
    return crm_service.conversation_tags.create(db, data)


@router.get("/{conversation_id}/tags", response_model=ListResponse[ConversationTagRead])
def list_tags(
    conversation_id: str,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return crm_service.conversation_tags.list_response(
        db, conversation_id, order_by, order_dir, limit, offset
    )
