"""Tests for field job location resolution."""

import uuid

from app.models.person import Person
from app.models.subscriber import Subscriber
from app.services.field import location as location_module
from app.services.field.location import resolve_job_location


def _attach_subscriber(db_session, work_order, person, **address):
    subscriber = Subscriber(
        person_id=person.id,
        service_address_line1=address.get("line1", "12 Admiralty Way"),
        service_city=address.get("city", "Lekki"),
        service_region=address.get("region", "Lagos"),
    )
    db_session.add(subscriber)
    db_session.flush()
    work_order.subscriber_id = subscriber.id
    db_session.commit()
    db_session.refresh(work_order)
    return subscriber


def test_geocode_success_caches_result(db_session, work_order, person, monkeypatch):
    _attach_subscriber(db_session, work_order, person)
    calls = []

    def _fake_geocode(db, data):
        calls.append(data)
        return {**data, "latitude": 6.4281, "longitude": 3.4216}

    monkeypatch.setattr(location_module.geocoding_service, "geocode_address", _fake_geocode)

    first = resolve_job_location(db_session, work_order)
    assert first["source"] == "geocoded"
    assert first["latitude"] == 6.4281
    assert "Admiralty" in first["address_text"]

    # Second call serves the cached value without re-geocoding.
    again = resolve_job_location(db_session, work_order)
    assert again["source"] == "cached"
    assert again["latitude"] == 6.4281
    assert len(calls) == 1

    db_session.refresh(work_order)
    assert work_order.metadata_["resolved_location"]["latitude"] == 6.4281


def test_geocode_failure_degrades_to_text_address(db_session, work_order, person, monkeypatch):
    _attach_subscriber(db_session, work_order, person)

    from fastapi import HTTPException

    def _boom(db, data):
        raise HTTPException(status_code=502, detail="Geocoding request failed")

    monkeypatch.setattr(location_module.geocoding_service, "geocode_address", _boom)

    result = resolve_job_location(db_session, work_order)
    assert result["source"] == "address_only"
    assert result["latitude"] is None
    assert "Admiralty" in result["address_text"]
    # Failures are not cached: the next call retries.
    db_session.refresh(work_order)
    assert not (work_order.metadata_ or {}).get("resolved_location")


def test_no_subscriber_means_no_location(db_session, work_order):
    result = resolve_job_location(db_session, work_order)
    assert result == {"latitude": None, "longitude": None, "address_text": None, "source": "none"}


def test_geocoder_returning_no_coords_is_address_only(db_session, work_order, person, monkeypatch):
    _attach_subscriber(db_session, work_order, person)
    monkeypatch.setattr(location_module.geocoding_service, "geocode_address", lambda db, data: data)

    result = resolve_job_location(db_session, work_order)
    assert result["source"] == "address_only"
    assert result["longitude"] is None


def test_person_address_fallback(db_session, work_order, monkeypatch):
    fallback_person = Person(
        first_name="Fallback",
        last_name="Person",
        email=f"fb-{uuid.uuid4().hex}@example.com",
        address_line1="7 Marina Road",
    )
    db_session.add(fallback_person)
    db_session.flush()
    subscriber = Subscriber(person_id=fallback_person.id)
    db_session.add(subscriber)
    db_session.flush()
    work_order.subscriber_id = subscriber.id
    db_session.commit()
    db_session.refresh(work_order)

    monkeypatch.setattr(
        location_module.geocoding_service,
        "geocode_address",
        lambda db, data: {**data, "latitude": 6.45, "longitude": 3.39},
    )
    result = resolve_job_location(db_session, work_order)
    assert result["source"] == "geocoded"
    assert "Marina" in result["address_text"]
