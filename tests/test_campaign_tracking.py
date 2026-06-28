"""Tests for per-recipient campaign open/click tracking."""

from __future__ import annotations

import uuid

from app.models.crm.campaign import Campaign, CampaignRecipient
from app.models.person import Person
from app.services import settings_spec
from app.services.crm.campaign_tracking import campaign_tracking

BASE = "https://t.example.com"
SECRET = "unit-test-signing-secret"


def _configure(monkeypatch, *, enabled=True, base_url=BASE, secret=SECRET):
    def fake(db, domain, key, use_cache=True):
        if key == "campaign_tracking_enabled":
            return enabled
        if key == "campaign_tracking_base_url":
            return base_url
        if key == "jwt_secret":
            return secret
        return None

    monkeypatch.setattr(settings_spec, "resolve_value", fake)


def _person(db) -> Person:
    p = Person(first_name="C", last_name="R", email=f"p-{uuid.uuid4().hex[:8]}@example.com")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _campaign(db) -> Campaign:
    c = Campaign(name="Tracking Promo")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _recipient(db, campaign, person) -> CampaignRecipient:
    r = CampaignRecipient(campaign_id=campaign.id, person_id=person.id, address=person.email or "x@example.com")
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


# --- signing -------------------------------------------------------------


def test_sign_verify_roundtrip():
    rid = str(uuid.uuid4())
    url = "https://dotmac.ng/promo?ref=1"
    sig = campaign_tracking.sign(rid, url, SECRET)
    assert campaign_tracking.verify(rid, url, sig, SECRET)
    # Tampered URL or wrong secret fails.
    assert not campaign_tracking.verify(rid, "https://evil.example/x", sig, SECRET)
    assert not campaign_tracking.verify(rid, url, sig, "other-secret")
    assert not campaign_tracking.verify(rid, url, "", SECRET)


def test_encode_decode_url_roundtrip():
    url = "https://dotmac.ng/path?a=1&b=two#frag"
    token = campaign_tracking._encode_url(url)
    assert campaign_tracking.decode_url(token) == url
    assert campaign_tracking.decode_url("!!!not-base64!!!") is None


# --- injection -----------------------------------------------------------


def test_inject_disabled_returns_unchanged(db_session, monkeypatch):
    _configure(monkeypatch, enabled=False)
    html = '<html><body><a href="https://dotmac.ng/x">Go</a></body></html>'
    out = campaign_tracking.inject_tracking(db_session, html, recipient_id=uuid.uuid4())
    assert out == html


def test_inject_missing_base_url_returns_unchanged(db_session, monkeypatch):
    _configure(monkeypatch, enabled=True, base_url=None)
    html = '<a href="https://dotmac.ng/x">Go</a>'
    out = campaign_tracking.inject_tracking(db_session, html, recipient_id=uuid.uuid4())
    assert out == html


def test_inject_rewrites_links_and_appends_pixel(db_session, monkeypatch):
    _configure(monkeypatch)
    rid = uuid.uuid4()
    url = "https://dotmac.ng/promo?ref=1"
    html = f'<html><body><p><a href="{url}">Go</a></p></body></html>'

    out = campaign_tracking.inject_tracking(db_session, html, recipient_id=rid)

    # Pixel inserted before </body>.
    assert f"{BASE}/track/email/o/{rid}.gif" in out
    assert out.index("/track/email/o/") < out.lower().index("</body>")
    # Link rewritten through the signed click endpoint; raw destination no longer a bare href.
    assert f'href="{url}"' not in out
    assert f"{BASE}/track/email/c/{rid}?u=" in out
    # The signature embedded in the rewritten link verifies for this destination.
    token = campaign_tracking._encode_url(url)
    sig = campaign_tracking.sign(str(rid), url, SECRET)
    assert f"u={token}&s={sig}" in out


def test_inject_pixel_appended_when_no_body_tag(db_session, monkeypatch):
    _configure(monkeypatch)
    rid = uuid.uuid4()
    out = campaign_tracking.inject_tracking(db_session, "plain text only", recipient_id=rid)
    assert out.endswith('.gif" width="1" height="1" alt="" style="display:none;border:0;width:1px;height:1px" />')


# --- recording -----------------------------------------------------------


def test_record_open_first_then_repeat(db_session):
    person = _person(db_session)
    campaign = _campaign(db_session)
    recipient = _recipient(db_session, campaign, person)

    assert campaign_tracking.record_open(db_session, recipient.id) is True
    db_session.refresh(recipient)
    db_session.refresh(campaign)
    assert recipient.opened_at is not None
    assert recipient.open_count == 1
    assert campaign.opened_count == 1
    first_opened_at = recipient.opened_at

    # Repeat open: counter increments, timestamp + campaign aggregate unchanged.
    assert campaign_tracking.record_open(db_session, recipient.id) is True
    db_session.refresh(recipient)
    db_session.refresh(campaign)
    assert recipient.open_count == 2
    assert recipient.opened_at == first_opened_at
    assert campaign.opened_count == 1


def test_record_open_unknown_recipient_returns_false(db_session):
    assert campaign_tracking.record_open(db_session, uuid.uuid4()) is False


def test_record_open_malformed_recipient_id_is_safe(db_session, monkeypatch):
    # A forged/garbage id in the pixel URL must not raise (endpoint stays 200).
    _configure(monkeypatch)
    assert campaign_tracking.record_open(db_session, "not-a-uuid") is False
    assert campaign_tracking.record_click(db_session, "not-a-uuid", "https://x.example", "sig") is None


def test_record_click_valid_signature(db_session, monkeypatch):
    _configure(monkeypatch)
    person = _person(db_session)
    campaign = _campaign(db_session)
    recipient = _recipient(db_session, campaign, person)
    url = "https://dotmac.ng/promo"
    sig = campaign_tracking.sign(str(recipient.id), url, SECRET)

    result = campaign_tracking.record_click(db_session, recipient.id, url, sig)

    assert result == url
    db_session.refresh(recipient)
    db_session.refresh(campaign)
    assert recipient.clicked_at is not None
    assert recipient.click_count == 1
    assert campaign.clicked_count == 1
    # A click also implies an open.
    assert recipient.opened_at is not None
    assert campaign.opened_count == 1


def test_record_click_invalid_signature_rejected(db_session, monkeypatch):
    _configure(monkeypatch)
    person = _person(db_session)
    campaign = _campaign(db_session)
    recipient = _recipient(db_session, campaign, person)

    result = campaign_tracking.record_click(db_session, recipient.id, "https://evil.example/x", "forged-signature")

    assert result is None
    db_session.refresh(recipient)
    db_session.refresh(campaign)
    assert recipient.clicked_at is None
    assert recipient.click_count == 0
    assert campaign.clicked_count == 0


def test_record_click_does_not_double_count_open(db_session, monkeypatch):
    _configure(monkeypatch)
    person = _person(db_session)
    campaign = _campaign(db_session)
    recipient = _recipient(db_session, campaign, person)

    # Open first, then click — the click must not re-increment the open aggregate.
    campaign_tracking.record_open(db_session, recipient.id)
    url = "https://dotmac.ng/promo"
    sig = campaign_tracking.sign(str(recipient.id), url, SECRET)
    campaign_tracking.record_click(db_session, recipient.id, url, sig)

    db_session.refresh(recipient)
    db_session.refresh(campaign)
    assert recipient.open_count == 1
    assert campaign.opened_count == 1
    assert recipient.click_count == 1
    assert campaign.clicked_count == 1
