from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.crm.contact import (
    ContactChannelCreate,
    ContactChannelRead,
    ContactChannelUpdate,
    ContactCreate,
    ContactRead,
    ContactUpdate,
)
from app.services import crm as crm_service

router = APIRouter(prefix="/crm/contacts", tags=["crm-contacts"])


@router.post("", response_model=ContactRead, status_code=status.HTTP_201_CREATED)
def create_contact(payload: ContactCreate, db: Session = Depends(get_db)):
    return crm_service.contacts.create(db, payload)


@router.get("/{contact_id}", response_model=ContactRead)
def get_contact(contact_id: str, db: Session = Depends(get_db)):
    return crm_service.contacts.get(db, contact_id)


@router.get("", response_model=ListResponse[ContactRead])
def list_contacts(
    person_id: str | None = None,
    organization_id: str | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return crm_service.contacts.list_response(
        db, person_id, organization_id, is_active, search, order_by, order_dir, limit, offset
    )


@router.patch("/{contact_id}", response_model=ContactRead)
def update_contact(contact_id: str, payload: ContactUpdate, db: Session = Depends(get_db)):
    return crm_service.contacts.update(db, contact_id, payload)


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(contact_id: str, db: Session = Depends(get_db)):
    crm_service.contacts.delete(db, contact_id)


@router.post(
    "/{contact_id}/channels",
    response_model=ContactChannelRead,
    status_code=status.HTTP_201_CREATED,
)
def create_contact_channel(
    contact_id: str,
    payload: ContactChannelCreate,
    db: Session = Depends(get_db),
):
    data = payload.model_copy(update={"person_id": contact_id})
    return crm_service.contact_channels.create(db, data)


@router.get("/{contact_id}/channels", response_model=ListResponse[ContactChannelRead])
def list_contact_channels(
    contact_id: str,
    channel_type: str | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return crm_service.contact_channels.list_response(db, contact_id, channel_type, order_by, order_dir, limit, offset)


@router.patch("/channels/{channel_id}", response_model=ContactChannelRead)
def update_contact_channel(
    channel_id: str,
    payload: ContactChannelUpdate,
    db: Session = Depends(get_db),
):
    return crm_service.contact_channels.update(db, channel_id, payload)
