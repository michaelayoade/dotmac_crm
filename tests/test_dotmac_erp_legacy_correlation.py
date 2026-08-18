"""Historical ERPNext identifiers remain correlation data for Dotmac ERP sync.

The executable ERPNext importer is retired.  These tests preserve only the
existing-data compatibility contract: CRM may include an old external
identifier in the typed Dotmac ERP payload without using it as a credential,
endpoint, or writer selector.
"""

import uuid
from unittest.mock import MagicMock

from app.models.event_store import EventStatus, EventStore
from app.models.person import Person
from app.models.projects import Project
from app.models.tickets import Ticket, TicketComment


class TestERPSyncPayloads:
    def test_ticket_payload_includes_erpnext_id(self, db_session):
        from app.services.dotmac_erp.sync import DotMacERPSync

        ticket = Ticket(
            title="Sync Test Ticket",
            erpnext_id="HD-TICKET-00042",
        )
        db_session.add(ticket)
        db_session.flush()

        sync = DotMacERPSync(db_session)
        payload = sync._map_ticket(ticket)

        assert payload["erpnext_id"] == "HD-TICKET-00042"
        assert payload["omni_id"] == str(ticket.id)

    def test_ticket_payload_erpnext_id_none(self, db_session):
        from app.services.dotmac_erp.sync import DotMacERPSync

        ticket = Ticket(title="No ERPNext Ticket")
        db_session.add(ticket)
        db_session.flush()

        sync = DotMacERPSync(db_session)
        payload = sync._map_ticket(ticket)
        assert payload["erpnext_id"] is None

    def test_ticket_payload_includes_description(self, db_session):
        from app.services.dotmac_erp.sync import DotMacERPSync

        ticket = Ticket(
            title="Description Sync Ticket",
            description="This is the ticket description sent to ERP.",
        )
        db_session.add(ticket)
        db_session.flush()

        sync = DotMacERPSync(db_session)
        payload = sync._map_ticket(ticket)
        assert payload["description"] == "This is the ticket description sent to ERP."

    def test_ticket_payload_includes_activity_log(self, db_session):
        from app.services.dotmac_erp.sync import DotMacERPSync

        author = Person(
            first_name="Tech",
            last_name="User",
            email="tech.user@example.com",
            erp_person_id="erp-person-001",
        )
        db_session.add(author)
        db_session.flush()

        ticket = Ticket(title="Activity Sync Ticket")
        db_session.add(ticket)
        db_session.flush()

        db_session.add(
            TicketComment(
                ticket_id=ticket.id,
                author_person_id=author.id,
                body="Technician note",
                is_internal=True,
            )
        )
        db_session.add(
            EventStore(
                event_id=uuid.uuid4(),
                event_type="ticket.updated",
                payload={"changed_fields": ["status"], "status": "open"},
                status=EventStatus.completed,
                ticket_id=ticket.id,
                is_active=True,
            )
        )
        db_session.commit()

        sync = DotMacERPSync(db_session)
        payload = sync._map_ticket(ticket)

        assert isinstance(payload["activity_log"], list)
        assert any(item.get("kind") == "comment" for item in payload["activity_log"])
        assert any(item.get("kind") == "event" for item in payload["activity_log"])
        assert isinstance(payload["comments"], list)
        assert any(item.get("body") == "Technician note" for item in payload["comments"])
        assert any(item.get("author_person_id") == "erp-person-001" for item in payload["comments"])
        assert any(
            item.get("kind") == "comment" and item.get("author_person_id") == "erp-person-001"
            for item in payload["activity_log"]
        )

    def test_ticket_payload_author_person_id_null_when_mapping_missing(self, db_session):
        from app.services.dotmac_erp.sync import DotMacERPSync

        author = Person(
            first_name="No",
            last_name="Mapping",
            email="no.mapping@example.com",
        )
        db_session.add(author)
        db_session.flush()

        ticket = Ticket(title="Missing Mapping Ticket")
        db_session.add(ticket)
        db_session.flush()

        db_session.add(
            TicketComment(
                ticket_id=ticket.id,
                author_person_id=author.id,
                body="No mapping for this author",
                is_internal=False,
            )
        )
        db_session.commit()

        sync = DotMacERPSync(db_session)
        payload = sync._map_ticket(ticket)
        assert payload["comments"][0]["author_person_id"] is None
        assert payload["comments"][0]["author_person_id"] != str(author.id)

    def test_ticket_payload_never_sends_local_author_id_when_erp_mapping_exists(self, db_session):
        from app.services.dotmac_erp.sync import DotMacERPSync

        author = Person(
            first_name="Mapped",
            last_name="Author",
            email="mapped.author@example.com",
            erp_person_id="erp-person-xyz",
        )
        db_session.add(author)
        db_session.flush()

        ticket = Ticket(title="Mapped Author Ticket")
        db_session.add(ticket)
        db_session.flush()

        db_session.add(
            TicketComment(
                ticket_id=ticket.id,
                author_person_id=author.id,
                body="Mapped author comment",
                is_internal=False,
            )
        )
        db_session.commit()

        sync = DotMacERPSync(db_session)
        payload = sync._map_ticket(ticket)

        assert payload["comments"][0]["author_person_id"] == "erp-person-xyz"
        assert payload["comments"][0]["author_person_id"] != str(author.id)

    def test_ticket_payload_resolves_author_from_erp_staff_email(self, db_session):
        from app.services.dotmac_erp.sync import DotMacERPSync

        author = Person(
            first_name="Staff",
            last_name="Mapped",
            email="staff.mapped@example.com",
        )
        db_session.add(author)
        db_session.flush()

        ticket = Ticket(title="Staff Author Ticket")
        db_session.add(ticket)
        db_session.flush()

        db_session.add(
            TicketComment(
                ticket_id=ticket.id,
                author_person_id=author.id,
                body="Staff comment",
                is_internal=False,
            )
        )
        db_session.commit()

        sync = DotMacERPSync(db_session)
        fake_client = MagicMock()
        fake_client.get_employees.return_value = [
            {
                "employee_id": "erp-staff-123",
                "email": "staff.mapped@example.com",
                "is_active": True,
            }
        ]
        sync._client = fake_client

        payload = sync._map_ticket(ticket)

        assert payload["comments"][0]["author_person_id"] == "erp-staff-123"
        assert payload["comments"][0]["author_person_id"] != str(author.id)

    def test_project_payload_includes_erpnext_id(self, db_session):
        from app.services.dotmac_erp.sync import DotMacERPSync

        project = Project(
            name="Sync Test Project",
            erpnext_id="PROJ-042",
        )
        db_session.add(project)
        db_session.flush()

        sync = DotMacERPSync(db_session)
        payload = sync._map_project(project)

        assert payload["erpnext_id"] == "PROJ-042"
        assert payload["omni_id"] == str(project.id)

    def test_project_payload_erpnext_id_none(self, db_session):
        from app.services.dotmac_erp.sync import DotMacERPSync

        project = Project(name="No ERPNext Project")
        db_session.add(project)
        db_session.flush()

        sync = DotMacERPSync(db_session)
        payload = sync._map_project(project)
        assert payload["erpnext_id"] is None


# ---------------------------------------------------------------------------
# ImportResult / ImportStats Tests
# ---------------------------------------------------------------------------
