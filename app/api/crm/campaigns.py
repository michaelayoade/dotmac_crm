"""Campaigns JSON API — thin wrappers over app.services.crm.campaigns.

Exposes the campaign domain (previously admin-web only) for programmatic /
external marketing-automation clients. Mounted under the CRM router, which is
gated by require_user_auth.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.schemas.common import ListResponse
from app.schemas.crm.campaign import (
    CampaignCreate,
    CampaignRead,
    CampaignRecipientRead,
    CampaignStepCreate,
    CampaignStepRead,
    CampaignStepUpdate,
    CampaignUpdate,
)
from app.services.crm.campaigns import campaign_recipients, campaign_steps, campaigns

router = APIRouter(prefix="/crm/campaigns", tags=["crm-campaigns"])


class CampaignScheduleRequest(BaseModel):
    scheduled_at: datetime


# ── campaigns ────────────────────────────────────────────────────────────────


@router.post("", response_model=CampaignRead, status_code=status.HTTP_201_CREATED)
def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db), auth=Depends(get_current_user)):
    created_by = str(auth["person_id"]) if auth and auth.get("person_id") else None
    return campaigns.create(db, payload, created_by_id=created_by)


@router.get("", response_model=ListResponse[CampaignRead])
def list_campaigns(
    status: str | None = None,
    campaign_type: str | None = None,
    search: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return campaigns.list_response(db, status, campaign_type, search, is_active, order_by, order_dir, limit, offset)


@router.get("/{campaign_id}", response_model=CampaignRead)
def get_campaign(campaign_id: str, db: Session = Depends(get_db)):
    return campaigns.get(db, campaign_id)


@router.patch("/{campaign_id}", response_model=CampaignRead)
def update_campaign(campaign_id: str, payload: CampaignUpdate, db: Session = Depends(get_db)):
    return campaigns.update(db, campaign_id, payload)


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_campaign(campaign_id: str, db: Session = Depends(get_db)):
    campaigns.delete(db, campaign_id)


@router.post("/{campaign_id}/schedule", response_model=CampaignRead)
def schedule_campaign(campaign_id: str, payload: CampaignScheduleRequest, db: Session = Depends(get_db)):
    return campaigns.schedule(db, campaign_id, payload.scheduled_at)


@router.post("/{campaign_id}/send", response_model=CampaignRead)
def send_campaign(campaign_id: str, db: Session = Depends(get_db)):
    return campaigns.send_now(db, campaign_id)


@router.post("/{campaign_id}/cancel", response_model=CampaignRead)
def cancel_campaign(campaign_id: str, db: Session = Depends(get_db)):
    return campaigns.cancel(db, campaign_id)


# ── steps ────────────────────────────────────────────────────────────────────


@router.get("/{campaign_id}/steps", response_model=ListResponse[CampaignStepRead])
def list_campaign_steps(
    campaign_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return campaign_steps.list_response(db, campaign_id, limit, offset)


@router.post("/steps", response_model=CampaignStepRead, status_code=status.HTTP_201_CREATED)
def create_campaign_step(payload: CampaignStepCreate, db: Session = Depends(get_db)):
    return campaign_steps.create(db, payload)


@router.get("/steps/{step_id}", response_model=CampaignStepRead)
def get_campaign_step(step_id: str, db: Session = Depends(get_db)):
    return campaign_steps.get(db, step_id)


@router.patch("/steps/{step_id}", response_model=CampaignStepRead)
def update_campaign_step(step_id: str, payload: CampaignStepUpdate, db: Session = Depends(get_db)):
    return campaign_steps.update(db, step_id, payload)


@router.delete("/steps/{step_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_campaign_step(step_id: str, db: Session = Depends(get_db)):
    campaign_steps.delete(db, step_id)


# ── recipients ───────────────────────────────────────────────────────────────


@router.get("/{campaign_id}/recipients", response_model=ListResponse[CampaignRecipientRead])
def list_campaign_recipients(
    campaign_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return campaign_recipients.list_response(db, campaign_id, limit, offset)
