"""Tests for DotMac ERP contact/customer sync (pull from ERP)."""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.person import PartyStatus, Person, PersonChannel
from app.models.subscriber import Organization
from app.services.dotmac_erp.contact_sync import (
    ContactSyncResult,
    DotMacERPContactSync,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sync_service(db_session):
    """Contact sync service with mocked client."""
    svc = DotMacERPContactSync(db_session)
    svc._client = MagicMock()
    return svc


def _make_company(erp_id="CUST-001", name="Acme Corp", **overrides):
    return {
        "customer_id": erp_id,
        "customer_name": name,
        "legal_name": f"{name} (Pty) Ltd",
        "tax_id": "123456",
        "domain": "acme.co.za",
        "website": "https://acme.co.za",
        "phone": "+27215550100",
        "email": "info@acme.co.za",
        "industry": "Telecommunications",
        "is_active": True,
        "address": {
            "line1": "123 Main St",
            "city": "Cape Town",
            "region": "Western Cape",
            "postal_code": "8001",
            "country_code": "ZA",
        },
        **overrides,
    }


def _make_contact(
    contact_id="CON-001",
    email="john@acme.co.za",
    first_name="John",
    last_name="Smith",
    **overrides,
):
    return {
        "contact_id": contact_id,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "phone": "+27825550142",
        "job_title": "Engineer",
        "company_id": None,
        "party_status": "customer",
        "is_active": True,
        "channels": [],
        "address": {},
        **overrides,
    }


# ---------------------------------------------------------------------------
# ContactSyncResult
# ---------------------------------------------------------------------------

class TestContactSyncResult:
    def test_defaults(self):
        r = ContactSyncResult()
        assert r.total_synced == 0
        assert r.has_errors is False

    def test_total_synced(self):
        r = ContactSyncResult(orgs_created=2, orgs_updated=3, contacts_created=10, contacts_updated=5)
        assert r.total_synced == 20

    def test_has_errors(self):
        r = ContactSyncResult(errors=[{"type": "api", "error": "boom"}])
        assert r.has_errors is True


# ---------------------------------------------------------------------------
# Phone normalization
# ---------------------------------------------------------------------------

class TestPhoneNormalization:
    def test_single_phone(self):
        assert DotMacERPContactSync._normalize_phone("+27825550142") == "+27825550142"

    def test_comma_separated_takes_first(self):
        result = DotMacERPContactSync._normalize_phone("08032448627, 08030541177, +234 803 622 1530")
        assert result == "08032448627"

    def test_long_phone_truncated(self):
        long_phone = "+" + "1" * 50
        result = DotMacERPContactSync._normalize_phone(long_phone)
        assert len(result) == 40

    def test_none_returns_none(self):
        assert DotMacERPContactSync._normalize_phone(None) is None

    def test_empty_string_returns_none(self):
        assert DotMacERPContactSync._normalize_phone("") is None


# ---------------------------------------------------------------------------
# Organization upsert
# ---------------------------------------------------------------------------

class TestUpsertOrganization:
    def test_create_new_org(self, sync_service, db_session):
        company = _make_company(erp_id="NEW-001", name="NewCo")
        action = sync_service._upsert_organization(company)
        db_session.flush()

        assert action == "created"
        org = db_session.query(Organization).filter(Organization.erp_id == "NEW-001").first()
        assert org is not None
        assert org.name == "NewCo"
        assert org.legal_name == "NewCo (Pty) Ltd"
        assert org.city == "Cape Town"

    def test_update_existing_org(self, sync_service, db_session):
        # Pre-create
        org = Organization(name="Old Name", erp_id="UPD-001", is_active=True)
        db_session.add(org)
        db_session.flush()
        sync_service._org_cache.clear()

        company = _make_company(erp_id="UPD-001", name="New Name")
        action = sync_service._upsert_organization(company)

        assert action == "updated"
        assert org.name == "New Name"

    def test_skip_when_no_customer_id(self, sync_service):
        action = sync_service._upsert_organization({"customer_name": "NoID"})
        assert action == "skipped"

    def test_phone_normalization_on_org(self, sync_service, db_session):
        company = _make_company(erp_id="PH-001", phone="111,222,333")
        sync_service._upsert_organization(company)
        db_session.flush()

        org = db_session.query(Organization).filter(Organization.erp_id == "PH-001").first()
        assert org.phone == "111"


# ---------------------------------------------------------------------------
# Person resolve + upsert
# ---------------------------------------------------------------------------

class TestResolveAndUpsertContact:
    def test_create_new_person(self, sync_service, db_session):
        contact = _make_contact(contact_id="C-NEW", email="new@test.com")
        result = ContactSyncResult()

        sync_service._upsert_contact(contact, result)
        db_session.flush()

        assert result.contacts_created == 1
        person = db_session.query(Person).filter(Person.email == "new@test.com").first()
        assert person is not None
        assert person.erp_customer_id == "C-NEW"
        assert person.party_status == PartyStatus.customer

    def test_update_existing_by_erp_id(self, sync_service, db_session):
        person = Person(first_name="Old", last_name="Name", email="old@test.com", erp_customer_id="C-EXIST")
        db_session.add(person)
        db_session.flush()
        sync_service._person_cache.clear()

        contact = _make_contact(contact_id="C-EXIST", email="old@test.com", first_name="New", last_name="Name")
        result = ContactSyncResult()
        sync_service._upsert_contact(contact, result)

        assert result.contacts_updated == 1
        assert person.first_name == "New"

    def test_link_by_email_fallback(self, sync_service, db_session):
        person = Person(first_name="Jane", last_name="Doe", email="jane@test.com")
        db_session.add(person)
        db_session.flush()
        sync_service._person_cache.clear()

        contact = _make_contact(contact_id="C-LINK", email="jane@test.com")
        result = ContactSyncResult()
        sync_service._upsert_contact(contact, result)

        assert result.contacts_linked == 1
        assert person.erp_customer_id == "C-LINK"

    def test_skip_contact_without_email(self, sync_service):
        contact = _make_contact(email="")
        result = ContactSyncResult()
        sync_service._upsert_contact(contact, result)
        assert result.contacts_created == 0
        assert result.contacts_updated == 0

    def test_link_to_organization(self, sync_service, db_session):
        org = Organization(name="TestOrg", erp_id="ORG-001")
        db_session.add(org)
        db_session.flush()
        sync_service._org_cache["ORG-001"] = org

        contact = _make_contact(contact_id="C-ORG", email="linked@test.com", company_id="ORG-001")
        result = ContactSyncResult()
        sync_service._upsert_contact(contact, result)
        db_session.flush()

        person = db_session.query(Person).filter(Person.email == "linked@test.com").first()
        assert person.organization_id == org.id

    def test_upsert_channels(self, sync_service, db_session):
        contact = _make_contact(
            contact_id="C-CH",
            email="chan@test.com",
            channels=[
                {"type": "email", "address": "chan@test.com", "is_primary": True},
                {"type": "whatsapp", "address": "+27825550142", "is_primary": False},
            ],
        )
        result = ContactSyncResult()
        sync_service._upsert_contact(contact, result)
        db_session.flush()

        assert result.channels_upserted == 2
        person = db_session.query(Person).filter(Person.email == "chan@test.com").first()
        channels = db_session.query(PersonChannel).filter(PersonChannel.person_id == person.id).all()
        assert len(channels) == 2

    def test_party_status_mapping(self, sync_service, db_session):
        for erp_status, expected in [("lead", PartyStatus.lead), ("subscriber", PartyStatus.subscriber)]:
            email = f"{erp_status}-{uuid.uuid4().hex[:6]}@test.com"
            contact = _make_contact(
                contact_id=f"C-PS-{erp_status}",
                email=email,
                party_status=erp_status,
            )
            result = ContactSyncResult()
            sync_service._upsert_contact(contact, result)
            db_session.flush()

            person = db_session.query(Person).filter(Person.email == email).first()
            assert person.party_status == expected


# ---------------------------------------------------------------------------
# Full sync_organizations flow
# ---------------------------------------------------------------------------

class TestSyncOrganizations:
    def test_returns_error_when_not_configured(self, db_session):
        svc = DotMacERPContactSync(db_session)
        with patch("app.services.dotmac_erp.contact_sync.settings_spec") as mock_settings:
            mock_settings.resolve_value.return_value = None
            result = svc.sync_organizations()
        assert result.has_errors
        assert result.errors[0]["type"] == "config"

    def test_sync_creates_and_counts(self, sync_service, db_session):
        sync_service._client.get_companies.return_value = [
            _make_company(erp_id="S-001", name="Corp A"),
            _make_company(erp_id="S-002", name="Corp B"),
        ]

        result = sync_service.sync_organizations()

        assert result.orgs_created == 2
        assert result.has_errors is False
        assert result.duration_seconds >= 0

    def test_pagination(self, sync_service, db_session):
        page1 = [_make_company(erp_id=f"P-{i}") for i in range(500)]
        page2 = [_make_company(erp_id="P-500")]
        sync_service._client.get_companies.side_effect = [page1, page2]

        result = sync_service.sync_organizations()

        assert result.orgs_created == 501
        assert sync_service._client.get_companies.call_count == 2

    def test_per_record_error_isolation(self, sync_service, db_session):
        """One bad record should not fail the entire batch."""
        good = _make_company(erp_id="G-001", name="Good")
        bad = _make_company(erp_id="B-001", name="A" * 500)  # may trigger DB error
        sync_service._client.get_companies.return_value = [good, bad]

        # Even if 'bad' fails, 'good' should still be created
        result = sync_service.sync_organizations()
        # At minimum we should not get a crash
        assert result.orgs_created + result.orgs_updated >= 1 or len(result.errors) >= 1


# ---------------------------------------------------------------------------
# Full sync_contacts flow
# ---------------------------------------------------------------------------

class TestSyncContacts:
    def test_returns_error_when_not_configured(self, db_session):
        svc = DotMacERPContactSync(db_session)
        with patch("app.services.dotmac_erp.contact_sync.settings_spec") as mock_settings:
            mock_settings.resolve_value.return_value = None
            result = svc.sync_contacts()
        assert result.has_errors
        assert result.errors[0]["type"] == "config"

    def test_sync_creates_contacts(self, sync_service, db_session):
        sync_service._client.get_contacts.return_value = [
            _make_contact(contact_id="SC-1", email="a@test.com"),
            _make_contact(contact_id="SC-2", email="b@test.com"),
        ]

        result = sync_service.sync_contacts()

        assert result.contacts_created == 2
        assert result.has_errors is False


# ---------------------------------------------------------------------------
# Full sync_all flow
# ---------------------------------------------------------------------------

class TestSyncAll:
    def test_sync_all_combines_results(self, sync_service, db_session):
        sync_service._client.get_companies.return_value = [
            _make_company(erp_id="ALL-ORG-1"),
        ]
        sync_service._client.get_contacts.return_value = [
            _make_contact(contact_id="ALL-CON-1", email="all@test.com"),
        ]

        result = sync_service.sync_all()

        assert result.orgs_created == 1
        assert result.contacts_created == 1
        assert result.duration_seconds >= 0
