"""Inbox source API endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.services.crm.inbox.inbox_sources import list_inbox_sources

router = APIRouter(tags=["inboxes"])


@router.get("/inboxes")
def get_inboxes(db: Session = Depends(get_db), _user=Depends(get_current_user)):
    return {"data": list_inbox_sources(db)}
