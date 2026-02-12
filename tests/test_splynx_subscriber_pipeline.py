"""Tests for the Splynx → Subscriber fulfillment pipeline.

Covers:
- SplynxCustomerHandler (event handler)
- Subscriber service: sync_from_external with sales_order_id, list_for_reseller
- splynx.py: fetch_customers, fetch_customer, ensure_person_customer
- Sync task: sync_subscribers_from_splynx
- Reseller model removal verification
"""

import uuid
from unittest.mock import MagicMock, patch

from app.models.person import PartyStatus, Person
from app.models.projects import Project
from app.models.sales_order import SalesOrder
from app.models.subscriber import (
    AccountType,
    Organization,
    Subscriber,
    SubscriberStatus,
)
from app.services.events.handlers.splynx_customer import (
    SplynxCustomerHandler,
    _ensure_subscriber,
    _resolve_person_for_project,
    _resolve_sales_order_id,
)
from app.services.events.types import Event, EventType
from app.services.splynx import (
    _build_customer_payload,
    _resolve_customer_url,
    ensure_person_customer,
)
from app.services.subscriber import subscriber as subscriber_service
from app.tasks.subscribers import _map_splynx_status

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex[:12]}@example.com"


def _make_person(db_session, **overrides) -> Person:
    data = {
        "first_name": "Test",
        "last_name": "Customer",
        "email": _unique_email(),
    }
    data.update(overrides)
    person = Person(**data)
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)
    return person


def _make_project(db_session, *, owner_person_id=None, metadata_=None) -> Project:
    project = Project(
        name="Test Fiber Install",
        owner_person_id=owner_person_id,
        metadata_=metadata_,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


def _make_sales_order(db_session, person_id) -> SalesOrder:
    so = SalesOrder(
        person_id=person_id,
        order_number=f"SO-{uuid.uuid4().hex[:8]}",
    )
    db_session.add(so)
    db_session.commit()
    db_session.refresh(so)
    return so


def _make_organization(db_session, *, account_type=AccountType.customer, parent_id=None, **kw) -> Organization:
    org = Organization(
        name=kw.get("name", f"Org-{uuid.uuid4().hex[:8]}"),
        account_type=account_type,
        parent_id=parent_id,
    )
    db_session.add(org)
    db_session.commit()
    db_session.refresh(org)
    return org


def _make_subscriber(db_session, *, person_id=None, organization_id=None, external_id=None, **kw) -> Subscriber:
    sub = Subscriber(
        person_id=person_id,
        organization_id=organization_id,
        external_system="splynx",
        external_id=external_id or str(uuid.uuid4().int)[:6],
        subscriber_number=kw.get("subscriber_number", f"SUB-{uuid.uuid4().hex[:6]}"),
        status=kw.get("status", SubscriberStatus.active),
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)
    return sub


def _make_splynx_customer(splynx_id="12345", **overrides):
    """Build a Splynx customer API response dict."""
    return {
        "id": splynx_id,
        "login": f"cust_{splynx_id}",
        "name": "John Doe",
        "email": f"john{splynx_id}@example.com",
        "phone": "+2348001234567",
        "status": "active",
        "tariff_name": "Fiber 100Mbps",
        "balance": "0.00",
        "currency": "NGN",
        "street": "123 Main St",
        "city": "Lagos",
        "state": "Lagos",
        "zip": "100001",
        **overrides,
    }


# ===========================================================================
# splynx.py unit tests
# ===========================================================================


class TestBuildCustomerPayload:
    def test_basic_payload(self, db_session):
        person = _make_person(db_session, first_name="Alice", last_name="Smith", phone="+2348001111111")
        payload = _build_customer_payload(person)
        assert payload["name"] == "Alice Smith"
        assert payload["email"] == person.email
        assert payload["phone"] == "+2348001111111"
        assert payload["status"] == "new"

    def test_display_name_preferred(self, db_session):
        person = _make_person(db_session, display_name="Big Boss")
        payload = _build_customer_payload(person)
        assert payload["name"] == "Big Boss"

    def test_fallback_name(self, db_session):
        person = _make_person(db_session, first_name="", last_name="", display_name=None)
        payload = _build_customer_payload(person)
        assert payload["name"] == "Customer"


class TestResolveCustomerUrl:
    def test_explicit_customer_url(self):
        config = {
            "base_url": "https://splynx.example.com/api/2.0",
            "customer_url": "https://splynx.example.com/api/2.0/admin/customers/customer",
        }
        assert _resolve_customer_url(config) == "https://splynx.example.com/api/2.0/admin/customers/customer"

    def test_auto_append_path(self):
        config = {"base_url": "https://splynx.example.com/api/2.0", "customer_url": None}
        assert _resolve_customer_url(config) == "https://splynx.example.com/api/2.0/admin/customers/customer"

    def test_no_duplicate_path(self):
        config = {"base_url": "https://splynx.example.com/api/2.0/admin/customers/customer", "customer_url": None}
        url = _resolve_customer_url(config)
        assert url == "https://splynx.example.com/api/2.0/admin/customers/customer"


class TestEnsurePersonCustomer:
    def test_sets_splynx_id_in_metadata(self, db_session):
        person = _make_person(db_session)
        ensure_person_customer(db_session, person, "42")
        db_session.refresh(person)
        assert person.metadata_["splynx_id"] == "42"

    def test_upgrades_lead_to_customer(self, db_session):
        person = _make_person(db_session)
        person.party_status = PartyStatus.lead
        db_session.commit()
        ensure_person_customer(db_session, person, "42")
        db_session.refresh(person)
        assert person.party_status == PartyStatus.customer

    def test_upgrades_contact_to_customer(self, db_session):
        person = _make_person(db_session)
        person.party_status = PartyStatus.contact
        db_session.commit()
        ensure_person_customer(db_session, person, "42")
        db_session.refresh(person)
        assert person.party_status == PartyStatus.customer

    def test_does_not_downgrade_subscriber(self, db_session):
        person = _make_person(db_session)
        person.party_status = PartyStatus.subscriber
        db_session.commit()
        ensure_person_customer(db_session, person, "42")
        db_session.refresh(person)
        assert person.party_status == PartyStatus.subscriber

    def test_initializes_metadata_if_none(self, db_session):
        person = _make_person(db_session)
        person.metadata_ = None
        db_session.commit()
        ensure_person_customer(db_session, person, "99")
        db_session.refresh(person)
        assert person.metadata_["splynx_id"] == "99"

    def test_no_splynx_id_still_upgrades_status(self, db_session):
        person = _make_person(db_session)
        person.party_status = PartyStatus.lead
        db_session.commit()
        ensure_person_customer(db_session, person, None)
        db_session.refresh(person)
        assert person.party_status == PartyStatus.customer


# ===========================================================================
# Subscriber service tests
# ===========================================================================


class TestSubscriberSyncFromExternal:
    def test_creates_new_subscriber(self, db_session):
        person = _make_person(db_session)
        sub = subscriber_service.sync_from_external(
            db_session,
            "splynx",
            "100",
            {
                "person_id": person.id,
                "status": "active",
                "subscriber_number": "100",
            },
        )
        assert sub.external_system == "splynx"
        assert sub.external_id == "100"
        assert sub.person_id == person.id

    def test_updates_existing_subscriber(self, db_session):
        person = _make_person(db_session)
        sub1 = subscriber_service.sync_from_external(
            db_session,
            "splynx",
            "101",
            {
                "person_id": person.id,
                "status": "active",
                "subscriber_number": "101",
            },
        )
        sub2 = subscriber_service.sync_from_external(
            db_session,
            "splynx",
            "101",
            {
                "person_id": person.id,
                "status": "suspended",
                "subscriber_number": "101-updated",
            },
        )
        assert sub1.id == sub2.id
        assert sub2.subscriber_number == "101-updated"

    def test_idempotent_no_duplicate(self, db_session):
        person = _make_person(db_session)
        subscriber_service.sync_from_external(
            db_session,
            "splynx",
            "102",
            {
                "person_id": person.id,
                "subscriber_number": "102",
            },
        )
        subscriber_service.sync_from_external(
            db_session,
            "splynx",
            "102",
            {
                "person_id": person.id,
                "subscriber_number": "102",
            },
        )
        results = (
            db_session.query(Subscriber)
            .filter(
                Subscriber.external_system == "splynx",
                Subscriber.external_id == "102",
            )
            .all()
        )
        assert len(results) == 1

    def test_sales_order_id_persisted(self, db_session):
        person = _make_person(db_session)
        so = _make_sales_order(db_session, person.id)
        sub = subscriber_service.sync_from_external(
            db_session,
            "splynx",
            "103",
            {
                "person_id": person.id,
                "subscriber_number": "103",
                "sales_order_id": so.id,
            },
        )
        assert sub.sales_order_id == so.id

    def test_person_subscribers_relationship(self, db_session):
        person = _make_person(db_session)
        subscriber_service.sync_from_external(
            db_session,
            "splynx",
            "104",
            {
                "person_id": person.id,
                "subscriber_number": "104",
            },
        )
        db_session.refresh(person)
        assert len(person.subscribers) == 1
        assert person.subscribers[0].external_id == "104"


class TestSubscriberListForReseller:
    def test_returns_subscribers_under_reseller_org(self, db_session):
        reseller_org = _make_organization(db_session, account_type=AccountType.reseller, name="Big ISP Reseller")
        child_org = _make_organization(
            db_session, account_type=AccountType.customer, parent_id=reseller_org.id, name="Child Corp"
        )

        person1 = _make_person(db_session)
        person2 = _make_person(db_session)
        _make_subscriber(db_session, person_id=person1.id, organization_id=reseller_org.id, external_id="r1")
        _make_subscriber(db_session, person_id=person2.id, organization_id=child_org.id, external_id="r2")

        results = subscriber_service.list_for_reseller(db_session, reseller_org.id)
        assert len(results) == 2

    def test_excludes_unrelated_orgs(self, db_session):
        reseller_org = _make_organization(db_session, account_type=AccountType.reseller, name="Reseller A")
        other_org = _make_organization(db_session, account_type=AccountType.customer, name="Unrelated Corp")

        person1 = _make_person(db_session)
        person2 = _make_person(db_session)
        _make_subscriber(db_session, person_id=person1.id, organization_id=reseller_org.id, external_id="x1")
        _make_subscriber(db_session, person_id=person2.id, organization_id=other_org.id, external_id="x2")

        results = subscriber_service.list_for_reseller(db_session, reseller_org.id)
        assert len(results) == 1
        assert results[0].organization_id == reseller_org.id

    def test_empty_when_no_subscribers(self, db_session):
        reseller_org = _make_organization(db_session, account_type=AccountType.reseller, name="Empty Reseller")
        results = subscriber_service.list_for_reseller(db_session, reseller_org.id)
        assert results == []


# ===========================================================================
# SplynxCustomerHandler tests
# ===========================================================================


class TestResolvePersonForProject:
    def test_resolves_from_owner(self, db_session):
        person = _make_person(db_session)
        project = _make_project(db_session, owner_person_id=person.id)
        result = _resolve_person_for_project(db_session, project)
        assert result is not None
        assert result.id == person.id

    def test_returns_none_for_orphan_project(self, db_session):
        project = _make_project(db_session)
        result = _resolve_person_for_project(db_session, project)
        assert result is None


class TestResolveSalesOrderId:
    def test_extracts_from_metadata(self, db_session):
        project = _make_project(db_session, metadata_={"sales_order_id": "abc-123"})
        assert _resolve_sales_order_id(project) == "abc-123"

    def test_returns_none_when_no_metadata(self, db_session):
        project = _make_project(db_session)
        assert _resolve_sales_order_id(project) is None

    def test_returns_none_when_key_missing(self, db_session):
        project = _make_project(db_session, metadata_={"other_key": "val"})
        assert _resolve_sales_order_id(project) is None


class TestEnsureSubscriberHelper:
    def test_creates_subscriber_via_sync(self, db_session):
        person = _make_person(db_session)
        _ensure_subscriber(db_session, person, "555")
        sub = subscriber_service.get_by_external_id(db_session, "splynx", "555")
        assert sub is not None
        assert sub.person_id == person.id
        assert sub.subscriber_number == "555"

    def test_with_sales_order_id(self, db_session):
        person = _make_person(db_session)
        so = _make_sales_order(db_session, person.id)
        _ensure_subscriber(db_session, person, "556", sales_order_id=str(so.id))
        sub = subscriber_service.get_by_external_id(db_session, "splynx", "556")
        assert sub is not None
        assert str(sub.sales_order_id) == str(so.id)

    def test_idempotent_call(self, db_session):
        person = _make_person(db_session)
        _ensure_subscriber(db_session, person, "557")
        _ensure_subscriber(db_session, person, "557")
        subs = (
            db_session.query(Subscriber)
            .filter(
                Subscriber.external_system == "splynx",
                Subscriber.external_id == "557",
            )
            .all()
        )
        assert len(subs) == 1


class TestSplynxCustomerHandlerHandle:
    @patch("app.services.events.handlers.splynx_customer.create_customer")
    @patch("app.services.events.handlers.splynx_customer.ensure_person_customer")
    def test_creates_customer_and_subscriber(self, mock_ensure, mock_create, db_session):
        person = _make_person(db_session)
        project = _make_project(db_session, owner_person_id=person.id)
        mock_create.return_value = "777"

        event = Event(
            event_type=EventType.project_created,
            payload={},
            project_id=project.id,
        )
        handler = SplynxCustomerHandler()
        handler.handle(db_session, event)

        mock_create.assert_called_once_with(db_session, person)
        mock_ensure.assert_called_once_with(db_session, person, "777")

        sub = subscriber_service.get_by_external_id(db_session, "splynx", "777")
        assert sub is not None
        assert sub.person_id == person.id

    @patch("app.services.events.handlers.splynx_customer.create_customer")
    @patch("app.services.events.handlers.splynx_customer.ensure_person_customer")
    def test_skips_if_splynx_id_exists(self, mock_ensure, mock_create, db_session):
        person = _make_person(db_session, metadata_={"splynx_id": "existing-42"})
        project = _make_project(db_session, owner_person_id=person.id)

        event = Event(
            event_type=EventType.project_created,
            payload={},
            project_id=project.id,
        )
        handler = SplynxCustomerHandler()
        handler.handle(db_session, event)

        mock_create.assert_not_called()
        mock_ensure.assert_called_once_with(db_session, person, "existing-42")

        sub = subscriber_service.get_by_external_id(db_session, "splynx", "existing-42")
        assert sub is not None

    @patch("app.services.events.handlers.splynx_customer.create_customer")
    def test_does_nothing_for_non_project_event(self, mock_create, db_session):
        event = Event(
            event_type=EventType.ticket_created,
            payload={},
        )
        handler = SplynxCustomerHandler()
        handler.handle(db_session, event)
        mock_create.assert_not_called()

    @patch("app.services.events.handlers.splynx_customer.create_customer")
    def test_does_nothing_when_no_person(self, mock_create, db_session):
        project = _make_project(db_session)  # no owner
        event = Event(
            event_type=EventType.project_created,
            payload={},
            project_id=project.id,
        )
        handler = SplynxCustomerHandler()
        handler.handle(db_session, event)
        mock_create.assert_not_called()

    @patch("app.services.events.handlers.splynx_customer.create_customer")
    @patch("app.services.events.handlers.splynx_customer.ensure_person_customer")
    def test_passes_sales_order_id_from_project_metadata(self, mock_ensure, mock_create, db_session):
        person = _make_person(db_session)
        so = _make_sales_order(db_session, person.id)
        project = _make_project(
            db_session,
            owner_person_id=person.id,
            metadata_={"sales_order_id": str(so.id)},
        )
        mock_create.return_value = "888"

        event = Event(
            event_type=EventType.project_created,
            payload={},
            project_id=project.id,
        )
        handler = SplynxCustomerHandler()
        handler.handle(db_session, event)

        sub = subscriber_service.get_by_external_id(db_session, "splynx", "888")
        assert sub is not None
        assert str(sub.sales_order_id) == str(so.id)

    @patch("app.services.events.handlers.splynx_customer.create_customer")
    def test_does_nothing_when_create_returns_none(self, mock_create, db_session):
        person = _make_person(db_session)
        project = _make_project(db_session, owner_person_id=person.id)
        mock_create.return_value = None

        event = Event(
            event_type=EventType.project_created,
            payload={},
            project_id=project.id,
        )
        handler = SplynxCustomerHandler()
        handler.handle(db_session, event)

        subs = db_session.query(Subscriber).filter(Subscriber.person_id == person.id).all()
        assert len(subs) == 0


# ===========================================================================
# Splynx sync task tests
# ===========================================================================


class TestMapSplynxStatus:
    def test_active_string(self):
        assert _map_splynx_status("active") == "active"

    def test_blocked_string(self):
        assert _map_splynx_status("blocked") == "suspended"

    def test_inactive_string(self):
        assert _map_splynx_status("inactive") == "terminated"

    def test_new_string(self):
        assert _map_splynx_status("new") == "pending"

    def test_active_int(self):
        assert _map_splynx_status(1) == "active"

    def test_suspended_int(self):
        assert _map_splynx_status(2) == "suspended"

    def test_terminated_int(self):
        assert _map_splynx_status(0) == "terminated"

    def test_none_defaults_to_active(self):
        assert _map_splynx_status(None) == "active"

    def test_unknown_defaults_to_active(self):
        assert _map_splynx_status("unknown_status") == "active"


class _NoCloseSession:
    """Wrapper to prevent the task from closing our test session."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._session, name)


class TestSyncSubscribersFromSplynxTask:
    @patch("app.services.splynx.fetch_customers")
    def test_sync_creates_subscribers(self, mock_fetch, db_session):
        """Test that the sync task creates subscribers from fetched data."""
        person = _make_person(db_session, email="john12345@example.com")
        person_id = person.id  # capture before session.close() detaches
        mock_fetch.return_value = [
            _make_splynx_customer("200", email="john12345@example.com"),
            _make_splynx_customer("201", email="unknown@example.com"),
        ]

        with patch("app.tasks.subscribers.SessionLocal", return_value=db_session):
            from app.tasks.subscribers import sync_subscribers_from_splynx

            results = sync_subscribers_from_splynx()

        assert results["created"] == 2
        assert results["updated"] == 0
        assert results["errors"] == []

        # Person-matched subscriber has person_id set
        sub_matched = subscriber_service.get_by_external_id(db_session, "splynx", "200")
        assert sub_matched is not None
        assert sub_matched.person_id == person_id

        # Unmatched subscriber has no person_id
        sub_unmatched = subscriber_service.get_by_external_id(db_session, "splynx", "201")
        assert sub_unmatched is not None
        assert sub_unmatched.person_id is None

    @patch("app.services.splynx.fetch_customers")
    def test_sync_updates_existing(self, mock_fetch, db_session):
        """Test that re-running sync updates rather than duplicates."""
        _make_subscriber(db_session, external_id="300", subscriber_number="old_login")

        mock_fetch.return_value = [
            _make_splynx_customer("300", login="new_login"),
        ]

        with patch("app.tasks.subscribers.SessionLocal", return_value=db_session):
            from app.tasks.subscribers import sync_subscribers_from_splynx

            results = sync_subscribers_from_splynx()

        assert results["updated"] == 1
        assert results["created"] == 0

        sub = subscriber_service.get_by_external_id(db_session, "splynx", "300")
        assert sub.subscriber_number == "new_login"

    @patch("app.services.splynx.fetch_customers")
    def test_sync_empty_data(self, mock_fetch, db_session):
        """Test graceful handling of empty API response."""
        mock_fetch.return_value = []

        with patch("app.tasks.subscribers.SessionLocal", return_value=db_session):
            from app.tasks.subscribers import sync_subscribers_from_splynx

            results = sync_subscribers_from_splynx()

        assert results["created"] == 0
        assert results["updated"] == 0


class TestSubscriberIdentityReconciliation:
    def test_links_subscriber_from_person_metadata(self, db_session):
        person = _make_person(
            db_session,
            metadata_={"splynx_id": "900"},
            party_status=PartyStatus.contact,
        )
        sub = _make_subscriber(
            db_session,
            person_id=None,
            external_id="900",
            subscriber_number="900",
        )

        link_results = subscriber_service.reconcile_external_people_links(db_session, external_system="splynx")
        status_results = subscriber_service.reconcile_party_status_from_subscribers(db_session)

        db_session.refresh(sub)
        db_session.refresh(person)

        assert link_results["linked_subscribers"] == 1
        assert sub.person_id == person.id
        assert status_results["upgraded_to_subscriber"] >= 1
        assert person.party_status == PartyStatus.subscriber

    def test_clears_duplicate_metadata_on_unlinked_duplicate(self, db_session):
        chosen = _make_person(
            db_session,
            metadata_={"splynx_id": "901"},
            party_status=PartyStatus.customer,
        )
        duplicate = _make_person(
            db_session,
            metadata_={"splynx_id": "901"},
            party_status=PartyStatus.contact,
        )
        _make_subscriber(
            db_session,
            person_id=chosen.id,
            external_id="901",
            subscriber_number="901",
        )

        results = subscriber_service.reconcile_external_people_links(
            db_session,
            external_system="splynx",
            clear_duplicate_metadata=True,
        )

        db_session.refresh(chosen)
        db_session.refresh(duplicate)
        assert results["duplicate_metadata_cleared"] >= 1
        assert chosen.metadata_ and chosen.metadata_.get("splynx_id") == "901"
        assert not duplicate.metadata_ or duplicate.metadata_.get("splynx_id") != "901"

    def test_downgrades_stale_subscriber_status_without_active_link(self, db_session):
        stale = _make_person(db_session, party_status=PartyStatus.subscriber)
        active = _make_person(db_session, party_status=PartyStatus.customer)
        _make_subscriber(
            db_session,
            person_id=active.id,
            external_id="902",
            subscriber_number="902",
        )

        results = subscriber_service.reconcile_party_status_from_subscribers(db_session)

        db_session.refresh(stale)
        db_session.refresh(active)
        assert results["upgraded_to_subscriber"] >= 1
        assert results["downgraded_to_customer"] >= 1
        assert stale.party_status == PartyStatus.customer
        assert active.party_status == PartyStatus.subscriber


# ===========================================================================
# splynx.py fetch functions (mocked HTTP)
# ===========================================================================


class TestFetchCustomers:
    @patch("app.services.splynx._get_config")
    def test_returns_empty_when_disabled(self, mock_config, db_session):
        mock_config.return_value = None
        from app.services.splynx import fetch_customers

        result = fetch_customers(db_session)
        assert result == []

    @patch("app.services.splynx._get_config")
    def test_returns_data_on_success(self, mock_config, db_session):
        mock_config.return_value = {
            "auth_type": "basic",
            "base_url": "https://splynx.test/api/2.0",
            "customer_url": None,
            "basic_token": "dGVzdDp0ZXN0",
            "timeout_seconds": 10,
        }

        fake_response = MagicMock()
        fake_response.json.return_value = [{"id": "1", "name": "Test"}]
        fake_response.raise_for_status = MagicMock()

        import requests as requests_mod

        with patch.object(requests_mod, "get", return_value=fake_response) as mock_get:
            from app.services.splynx import fetch_customers

            result = fetch_customers(db_session)

        assert result == [{"id": "1", "name": "Test"}]
        mock_get.assert_called_once()

    @patch("app.services.splynx._get_config")
    def test_returns_empty_on_http_error(self, mock_config, db_session):
        mock_config.return_value = {
            "auth_type": "basic",
            "base_url": "https://splynx.test/api/2.0",
            "customer_url": None,
            "basic_token": "dGVzdDp0ZXN0",
            "timeout_seconds": 10,
        }

        import requests as requests_mod

        with patch.object(requests_mod, "get", side_effect=requests_mod.RequestException("timeout")):
            from app.services.splynx import fetch_customers

            result = fetch_customers(db_session)

        assert result == []


class TestFetchCustomer:
    @patch("app.services.splynx._get_config")
    def test_returns_none_when_disabled(self, mock_config, db_session):
        mock_config.return_value = None
        from app.services.splynx import fetch_customer

        result = fetch_customer(db_session, "123")
        assert result is None

    @patch("app.services.splynx._get_config")
    def test_returns_customer_on_success(self, mock_config, db_session):
        mock_config.return_value = {
            "auth_type": "basic",
            "base_url": "https://splynx.test/api/2.0",
            "customer_url": None,
            "basic_token": "dGVzdDp0ZXN0",
            "timeout_seconds": 10,
        }

        fake_response = MagicMock()
        fake_response.json.return_value = {"id": "123", "name": "Test Customer"}
        fake_response.raise_for_status = MagicMock()

        import requests as requests_mod

        with patch.object(requests_mod, "get", return_value=fake_response) as mock_get:
            from app.services.splynx import fetch_customer

            result = fetch_customer(db_session, "123")

        assert result == {"id": "123", "name": "Test Customer"}
        call_url = mock_get.call_args[0][0]
        assert call_url.endswith("/123")


# ===========================================================================
# Reseller model removal verification
# ===========================================================================


class TestResellerModelRemoved:
    def test_reseller_not_importable_from_subscriber_module(self):
        """Verify the Reseller class no longer exists in subscriber module."""
        from app.models import subscriber as sub_module

        assert not hasattr(sub_module, "Reseller")
        assert not hasattr(sub_module, "ResellerUser")

    def test_reseller_not_in_models_init(self):
        """Verify Reseller is not exported from models __init__."""
        import app.models as models

        assert not hasattr(models, "Reseller")
        assert not hasattr(models, "ResellerUser")

    def test_account_type_reseller_still_exists(self):
        """The enum value should remain for Organization use."""
        assert AccountType.reseller.value == "reseller"

    def test_organization_can_be_reseller(self, db_session):
        """Verify Organization(account_type=reseller) works as replacement."""
        org = _make_organization(db_session, account_type=AccountType.reseller, name="ISP Partner")
        db_session.refresh(org)
        assert org.account_type == AccountType.reseller
