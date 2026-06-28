"""Campaigns JSON API — thin wrappers over the campaigns service managers."""

from datetime import UTC, datetime

from app.api.crm import campaigns as campaigns_api
from app.schemas.crm.campaign import CampaignCreate, CampaignStepCreate, CampaignUpdate


def _create(db_session, name="Spring promo"):
    return campaigns_api.create_campaign(CampaignCreate(name=name), db_session, {"person_id": None})


def test_campaign_crud_and_list(db_session):
    created = _create(db_session)
    cid = str(created.id)
    assert created.status.value == "draft"

    assert campaigns_api.get_campaign(cid, db_session).id == created.id

    listed = campaigns_api.list_campaigns(order_by="created_at", order_dir="desc", limit=50, offset=0, db=db_session)
    assert listed["count"] >= 1
    assert any(str(campaign.id) == cid for campaign in listed["items"])

    updated = campaigns_api.update_campaign(cid, CampaignUpdate(subject="Hello"), db_session)
    assert updated.subject == "Hello"


def test_campaign_schedule_then_cancel(db_session):
    cid = str(_create(db_session, "Scheduled").id)
    scheduled_at = datetime(2099, 1, 1, 9, 0, tzinfo=UTC)
    scheduled = campaigns_api.schedule_campaign(
        cid,
        campaigns_api.CampaignScheduleRequest(scheduled_at=scheduled_at),
        db_session,
    )
    assert scheduled.status.value == "scheduled"

    cancelled = campaigns_api.cancel_campaign(cid, db_session)
    assert cancelled.status.value == "cancelled"


def test_campaign_steps(db_session):
    # Steps are a nurture-campaign feature.
    campaign = campaigns_api.create_campaign(
        CampaignCreate(name="With steps", campaign_type="nurture"),
        db_session,
        {"person_id": None},
    )
    step = campaigns_api.create_campaign_step(
        CampaignStepCreate(campaign_id=str(campaign.id), step_index=0, name="Day 1"),
        db_session,
    )
    assert step.name == "Day 1"
    steps = campaigns_api.list_campaign_steps(str(campaign.id), limit=50, offset=0, db=db_session)
    assert steps["count"] == 1


def test_recipients_endpoint(db_session):
    cid = str(_create(db_session, "Recip").id)
    recipients = campaigns_api.list_campaign_recipients(cid, limit=50, offset=0, db=db_session)
    assert recipients["count"] == 0
