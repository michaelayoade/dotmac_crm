"""Tests for campaign → lead attribution + ROI report."""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.models.crm.campaign import Campaign, CampaignRecipient
from app.models.crm.enums import CampaignRecipientStatus, LeadStatus
from app.models.crm.sales import Lead
from app.models.person import Person
from app.services.crm import campaigns as campaigns_mod
from app.services.crm.campaigns import attribute_lead_from_reply, campaign_attribution_report


def _enable(monkeypatch, enabled: bool = True):
    monkeypatch.setattr(
        campaigns_mod.settings_spec,
        "resolve_value",
        lambda db, domain, key, use_cache=True: enabled,
    )


def _person(db, email: str | None = None) -> Person:
    p = Person(first_name="C", last_name="R", email=email or f"p-{uuid.uuid4().hex[:8]}@example.com")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _campaign(db, name: str = "Q3 Promo") -> Campaign:
    c = Campaign(name=name)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_reply_creates_campaign_sourced_lead(db_session, monkeypatch):
    _enable(monkeypatch)
    person = _person(db_session)
    campaign = _campaign(db_session)

    lead = attribute_lead_from_reply(db_session, person_id=person.id, campaign_id=campaign.id)

    assert lead is not None
    assert lead.campaign_id == campaign.id
    assert lead.lead_source == "Campaign"
    assert lead.status == LeadStatus.new


def test_attribution_reuses_open_lead(db_session, monkeypatch):
    _enable(monkeypatch)
    person = _person(db_session)
    campaign = _campaign(db_session)
    existing = Lead(person_id=person.id, title="Existing", status=LeadStatus.qualified, lead_source="Website")
    db_session.add(existing)
    db_session.commit()
    db_session.refresh(existing)

    lead = attribute_lead_from_reply(db_session, person_id=person.id, campaign_id=campaign.id)

    assert lead.id == existing.id  # no duplicate pipeline entry
    assert lead.campaign_id == campaign.id
    assert lead.lead_source == "Website"  # preserved (already set)
    assert db_session.query(Lead).filter(Lead.person_id == person.id).count() == 1


def test_attribution_is_idempotent(db_session, monkeypatch):
    _enable(monkeypatch)
    person = _person(db_session)
    campaign = _campaign(db_session)

    first = attribute_lead_from_reply(db_session, person_id=person.id, campaign_id=campaign.id)
    second = attribute_lead_from_reply(db_session, person_id=person.id, campaign_id=campaign.id)

    assert first.id == second.id
    assert db_session.query(Lead).filter(Lead.campaign_id == campaign.id).count() == 1


def test_attribution_disabled_returns_none(db_session, monkeypatch):
    _enable(monkeypatch, enabled=False)
    person = _person(db_session)
    campaign = _campaign(db_session)

    assert attribute_lead_from_reply(db_session, person_id=person.id, campaign_id=campaign.id) is None
    assert db_session.query(Lead).count() == 0


def test_attribution_report_aggregates(db_session, monkeypatch):
    _enable(monkeypatch)
    campaign = _campaign(db_session)

    for _ in range(2):
        p = _person(db_session)
        db_session.add(
            CampaignRecipient(
                campaign_id=campaign.id,
                person_id=p.id,
                address=p.email,
                status=CampaignRecipientStatus.sent,
            )
        )
    db_session.commit()

    pw = _person(db_session)
    db_session.add(
        Lead(
            person_id=pw.id,
            title="Won",
            status=LeadStatus.won,
            campaign_id=campaign.id,
            estimated_value=Decimal("10000"),
        )
    )
    po = _person(db_session)
    db_session.add(
        Lead(
            person_id=po.id,
            title="Open",
            status=LeadStatus.new,
            campaign_id=campaign.id,
            estimated_value=Decimal("5000"),
        )
    )
    db_session.commit()

    report = campaign_attribution_report(db_session, str(campaign.id))
    assert report["recipients"] == 2
    assert report["sent"] == 2
    assert report["leads_attributed"] == 2
    assert report["leads_won"] == 1
    assert report["leads_open"] == 1
    assert report["won_value"] == Decimal("10000")
    assert report["pipeline_value"] == Decimal("5000")
