import httpx

from app.models.crm.campaign import Campaign, CampaignRecipient
from app.models.crm.enums import CampaignChannel, CampaignStatus
from app.models.crm.sales import Lead
from app.models.person import Person
from app.services.crm.serp_targets import seed_campaign_from_serp
from app.services.crm.web_campaigns import keep_selected_campaign_recipients


def _serp_response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        request=httpx.Request("GET", "https://serpapi.com/search.json"),
    )


def test_seed_campaign_from_serp_creates_leads_and_recipients(db_session, monkeypatch):
    campaign = Campaign(
        name="SERP Campaign",
        channel=CampaignChannel.email,
        status=CampaignStatus.draft,
        subject="Hello",
    )
    db_session.add(campaign)
    db_session.commit()

    def fake_get(url, params, timeout):
        assert url == "https://serpapi.com/search.json"
        assert params["engine"] == "google_maps"
        assert params["type"] == "search"
        assert params["q"] == "fiber installers Lagos"
        assert params["api_key"] == "test-key"
        assert timeout == 20.0
        return _serp_response(
            {
                "local_results": [
                    {
                        "position": 1,
                        "title": "Acme Fiber",
                        "website": "https://www.acme.example/services",
                        "description": "Enterprise fiber deployment.",
                    },
                    {
                        "position": 2,
                        "title": "Beta Networks",
                        "website": "https://beta.example",
                        "description": "Contact sales@beta.example for projects.",
                    },
                ]
            }
        )

    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")
    monkeypatch.setattr("app.services.crm.serp_targets.httpx.get", fake_get)

    result = seed_campaign_from_serp(
        db_session,
        campaign_id=str(campaign.id),
        query="fiber installers Lagos",
        location="Lagos, Nigeria",
        max_results=10,
        email_pattern="info@{domain}",
    )

    assert result == {"selected": 2, "seeded": 2, "skipped": 0}
    assert db_session.query(Person).filter(Person.email.in_(["info@acme.example", "sales@beta.example"])).count() == 2
    assert db_session.query(Lead).count() == 2
    assert db_session.query(CampaignRecipient).filter(CampaignRecipient.campaign_id == campaign.id).count() == 2

    db_session.refresh(campaign)
    assert campaign.total_recipients == 2
    assert campaign.metadata_["source_report"] == "serp_google"
    assert campaign.metadata_["serp_last_query"]["engine"] == "google_maps"
    assert campaign.metadata_["serp_last_query"]["search_type"] == "search"
    assert campaign.metadata_["serp_last_query"]["result_kind"] == "business"
    assert campaign.metadata_["audience_snapshot_count"] == 2


def test_seed_campaign_from_serp_rejects_non_draft_campaign(db_session, monkeypatch):
    campaign = Campaign(
        name="Sent Campaign",
        channel=CampaignChannel.email,
        status=CampaignStatus.sending,
    )
    db_session.add(campaign)
    db_session.commit()
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")

    try:
        seed_campaign_from_serp(
            db_session,
            campaign_id=str(campaign.id),
            query="fiber installers Lagos",
            location="",
            max_results=10,
            email_pattern="info@{domain}",
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
        assert "draft" in str(getattr(exc, "detail", "")).lower()
    else:
        raise AssertionError("Expected non-draft campaign to be rejected")


def test_seed_campaign_from_serp_retries_without_unsupported_location(db_session, monkeypatch):
    campaign = Campaign(
        name="SERP Campaign",
        channel=CampaignChannel.email,
        status=CampaignStatus.draft,
        subject="Hello",
    )
    db_session.add(campaign)
    db_session.commit()
    calls = []

    def fake_get(url, params, timeout):
        calls.append(dict(params))
        if "location" in params:
            return _serp_response({"error": "Unsupported `fct, nigeria` location - location parameter."})
        return _serp_response(
            {
                "local_results": [
                    {
                        "position": 1,
                        "title": "Abuja Fiber",
                        "website": "https://abuja-fiber.example",
                        "description": "Enterprise connectivity.",
                    }
                ]
            }
        )

    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")
    monkeypatch.setattr("app.services.crm.serp_targets.httpx.get", fake_get)

    result = seed_campaign_from_serp(
        db_session,
        campaign_id=str(campaign.id),
        query="fiber installers Abuja",
        location="fct, nigeria",
        max_results=10,
        email_pattern="info@{domain}",
    )

    assert result == {"selected": 1, "seeded": 1, "skipped": 0}
    assert len(calls) == 2
    assert calls[0]["engine"] == "google_maps"
    assert calls[0]["type"] == "search"
    assert calls[0]["location"] == "Abuja, Federal Capital Territory, Nigeria"
    assert "location" not in calls[1]
    assert db_session.query(Person).filter(Person.email == "info@abuja-fiber.example").count() == 1


def test_seed_campaign_from_serp_adds_whatsapp_recipients_from_local_results(db_session, monkeypatch):
    campaign = Campaign(
        name="SERP WhatsApp Campaign",
        channel=CampaignChannel.whatsapp,
        status=CampaignStatus.draft,
    )
    db_session.add(campaign)
    db_session.commit()

    def fake_get(url, params, timeout):
        assert params["engine"] == "google_maps"
        assert params["type"] == "search"
        assert params["q"] == "schools in Gwarimpa"
        assert params["location"] == "Gwarinpa, Abuja, Federal Capital Territory, Nigeria"
        assert params["z"] == 15
        return _serp_response(
            {
                "local_results": [
                    {
                        "position": 1,
                        "title": "Gwarimpa Model School",
                        "website": "https://gwarimpamodel.example",
                        "phone": "+234 803 123 4567",
                        "address": "Gwarimpa, Abuja",
                    }
                ],
            }
        )

    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")
    monkeypatch.setattr("app.services.crm.serp_targets.httpx.get", fake_get)

    result = seed_campaign_from_serp(
        db_session,
        campaign_id=str(campaign.id),
        query="schools in Gwarimpa",
        location="FCT, Nigeria",
        max_results=10,
        email_pattern="info@{domain}",
    )

    assert result == {"selected": 1, "seeded": 1, "skipped": 0}
    person = db_session.query(Person).filter(Person.phone == "+2348031234567").one()
    assert person.email == "info@gwarimpamodel.example"
    recipient = db_session.query(CampaignRecipient).filter(CampaignRecipient.campaign_id == campaign.id).one()
    assert recipient.address == "+2348031234567"
    assert recipient.email is None


def test_keep_selected_campaign_recipients_removes_unselected_draft_recipients(db_session):
    campaign = Campaign(
        name="Selective Send",
        channel=CampaignChannel.whatsapp,
        status=CampaignStatus.draft,
    )
    people = [
        Person(first_name="One", last_name="", display_name="One Hospital", email="one@example.test", phone="0801"),
        Person(first_name="Two", last_name="", display_name="Two Hospital", email="two@example.test", phone="0802"),
    ]
    db_session.add(campaign)
    db_session.add_all(people)
    db_session.flush()
    recipients = [
        CampaignRecipient(campaign_id=campaign.id, person_id=people[0].id, address="0801"),
        CampaignRecipient(campaign_id=campaign.id, person_id=people[1].id, address="0802"),
    ]
    db_session.add_all(recipients)
    campaign.total_recipients = 2
    campaign.metadata_ = {
        "audience_snapshot": [
            {"person_id": str(people[0].id), "name": "One Hospital"},
            {"person_id": str(people[1].id), "name": "Two Hospital"},
        ],
        "audience_snapshot_count": 2,
    }
    db_session.commit()

    result = keep_selected_campaign_recipients(
        db_session,
        campaign_id=str(campaign.id),
        recipient_ids=[str(recipients[0].id)],
    )

    assert result == {"kept": 1, "removed": 1}
    remaining = db_session.query(CampaignRecipient).filter(CampaignRecipient.campaign_id == campaign.id).one()
    assert remaining.id == recipients[0].id
    db_session.refresh(campaign)
    assert campaign.total_recipients == 1
    assert campaign.metadata_["audience_snapshot_count"] == 1
    assert campaign.metadata_["audience_snapshot"][0]["person_id"] == str(people[0].id)
