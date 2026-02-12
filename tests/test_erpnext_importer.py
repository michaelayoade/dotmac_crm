"""Tests for ERPNext importer enhancements.

Covers:
- New mapper functions (map_hd_ticket_comment, map_communication, map_project_comment)
- erpnext_id setting on imported models (Ticket, Project, ProjectTask, Person, Organization)
- DotMac number generation during import (tickets, projects, tasks)
- Comment and communication import from child tables
- Idempotent re-import (ExternalReference dedup)
- Splynx ID capture from ERPNext Customer custom fields
- DotMac ERP sync payloads include erpnext_id
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.external import ExternalEntityType, ExternalReference
from app.models.person import Person
from app.models.projects import Project, ProjectComment, ProjectTask, ProjectTaskComment
from app.models.subscriber import Organization, Subscriber
from app.models.tickets import Ticket, TicketComment
from app.services.erpnext.importer import ERPNextImporter, ImportResult, ImportStats
from app.services.erpnext.mappers import (
    map_communication,
    map_customer,
    map_hd_ticket,
    map_hd_ticket_comment,
    map_project,
    map_project_comment,
    map_task,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def connector_config(db_session):
    """Connector config for ExternalReference FK."""
    cfg = ConnectorConfig(
        name="ERPNext Test",
        connector_type=ConnectorType.custom,
        is_active=True,
    )
    db_session.add(cfg)
    db_session.commit()
    db_session.refresh(cfg)
    return cfg


@pytest.fixture()
def importer(connector_config):
    """ERPNextImporter with mocked client."""
    imp = ERPNextImporter(
        base_url="https://erp.test.local",
        api_key="key",
        api_secret="secret",
        connector_config_id=connector_config.id,
    )
    imp.client = MagicMock()
    return imp


# ---------------------------------------------------------------------------
# Mapper Tests
# ---------------------------------------------------------------------------


class TestMapHdTicketComment:
    def test_basic_comment(self):
        doc = {
            "name": "row-001",
            "comment": "<p>Issue resolved</p>",
            "comment_by": "admin@test.com",
            "comment_type": "Comment",
            "creation": "2024-01-15 10:00:00",
        }
        result = map_hd_ticket_comment(doc)
        assert result["body"] == "Issue resolved"
        assert result["is_internal"] is False
        assert result["_erpnext_name"] == "row-001"
        assert result["_erpnext_comment_by"] == "admin@test.com"

    def test_info_type_is_internal(self):
        doc = {
            "name": "row-002",
            "comment": "Status changed to Open",
            "comment_type": "Info",
        }
        result = map_hd_ticket_comment(doc)
        assert result["is_internal"] is True

    def test_html_stripping(self):
        doc = {
            "name": "row-003",
            "comment": "<div><b>Bold</b> text with <a href='#'>link</a></div>",
            "comment_type": "Comment",
        }
        result = map_hd_ticket_comment(doc)
        assert "<" not in result["body"]
        assert "Bold" in result["body"]

    def test_content_fallback(self):
        doc = {
            "name": "row-004",
            "content": "Fallback content field",
            "comment_type": "Comment",
        }
        result = map_hd_ticket_comment(doc)
        assert result["body"] == "Fallback content field"

    def test_empty_comment(self):
        doc = {"name": "row-005", "comment": "", "comment_type": "Comment"}
        result = map_hd_ticket_comment(doc)
        assert result["body"] == ""


class TestMapCommunication:
    def test_basic_communication(self):
        doc = {
            "name": "COMM-001",
            "subject": "Re: Fiber issue",
            "content": "<p>Thanks for the update</p>",
            "sender": "customer@example.com",
            "sent_or_received": "Received",
            "creation": "2024-01-15 11:00:00",
        }
        result = map_communication(doc)
        assert "**Re: Fiber issue**" in result["body"]
        assert "Thanks for the update" in result["body"]
        assert result["is_internal"] is False
        assert result["_erpnext_sender"] == "customer@example.com"

    def test_sent_is_internal(self):
        doc = {
            "name": "COMM-002",
            "subject": "Update",
            "content": "We fixed the issue",
            "sent_or_received": "Sent",
        }
        result = map_communication(doc)
        assert result["is_internal"] is True

    def test_no_subject(self):
        doc = {
            "name": "COMM-003",
            "subject": "",
            "content": "<p>Body only</p>",
            "sent_or_received": "Received",
        }
        result = map_communication(doc)
        assert result["body"] == "Body only"
        assert "**" not in result["body"]


class TestMapProjectComment:
    def test_basic_project_comment(self):
        doc = {
            "name": "pc-001",
            "comment": "Progress update: 50% complete",
            "comment_by": "pm@test.com",
            "comment_type": "Comment",
            "creation": "2024-02-01 09:00:00",
        }
        result = map_project_comment(doc)
        assert result["body"] == "Progress update: 50% complete"
        assert result["is_internal"] is False
        assert result["_erpnext_name"] == "pc-001"

    def test_info_type(self):
        doc = {
            "name": "pc-002",
            "comment": "Status changed",
            "comment_type": "Info",
        }
        result = map_project_comment(doc)
        assert result["is_internal"] is True


class TestMapCustomerSplynxId:
    def test_splynx_id_from_custom_field(self):
        doc = {
            "name": "CUST-001",
            "customer_name": "Acme ISP",
            "custom_splynx_id": "42",
        }
        result = map_customer(doc)
        assert result["_erpnext_splynx_id"] == "42"

    def test_splynx_customer_id_fallback(self):
        doc = {
            "name": "CUST-002",
            "customer_name": "Beta ISP",
            "custom_splynx_customer_id": "99",
        }
        result = map_customer(doc)
        assert result["_erpnext_splynx_id"] == "99"

    def test_no_splynx_id(self):
        doc = {
            "name": "CUST-003",
            "customer_name": "Gamma ISP",
        }
        result = map_customer(doc)
        assert result["_erpnext_splynx_id"] is None


class TestMapHdTicket:
    def test_erpnext_name_preserved(self):
        doc = {
            "name": "HD-TICKET-00001",
            "subject": "Network down",
            "status": "Open",
            "priority": "High",
        }
        result = map_hd_ticket(doc)
        assert result["_erpnext_name"] == "HD-TICKET-00001"
        assert result["title"] == "Network down"

    def test_customer_field(self):
        doc = {
            "name": "HD-TICKET-00002",
            "subject": "Slow speeds",
            "customer": "CUST-001",
        }
        result = map_hd_ticket(doc)
        assert result["_erpnext_customer"] == "CUST-001"


class TestMapProject:
    def test_erpnext_name(self):
        doc = {
            "name": "PROJ-001",
            "project_name": "Fiber Rollout Phase 2",
            "status": "Open",
        }
        result = map_project(doc)
        assert result["_erpnext_name"] == "PROJ-001"
        assert result["name"] == "Fiber Rollout Phase 2"


class TestMapTask:
    def test_erpnext_name(self):
        doc = {
            "name": "TASK-001",
            "subject": "Splice segment A",
            "status": "Open",
            "project": "PROJ-001",
        }
        result = map_task(doc)
        assert result["_erpnext_name"] == "TASK-001"
        assert result["name"] == "Splice segment A"


# ---------------------------------------------------------------------------
# Importer Integration Tests
# ---------------------------------------------------------------------------


class TestImportContacts:
    def test_new_contact_sets_erpnext_id(self, db_session, importer):
        importer.client.get_all.return_value = iter(
            [
                {
                    "name": "CONTACT-001",
                    "first_name": "John",
                    "last_name": "Doe",
                    "email_id": f"john-{uuid.uuid4().hex[:8]}@example.com",
                }
            ]
        )

        stats = importer._import_contacts(db_session)
        assert stats.created == 1

        person = db_session.query(Person).filter(Person.erpnext_id == "CONTACT-001").first()
        assert person is not None
        assert person.first_name == "John"
        assert person.erpnext_id == "CONTACT-001"

    def test_existing_person_by_erpnext_id_skips(self, db_session, importer):
        """If Person already has erpnext_id matching, skip creation."""
        email = f"existing-{uuid.uuid4().hex[:8]}@example.com"
        person = Person(
            first_name="Jane",
            last_name="Existing",
            email=email,
            erpnext_id="CONTACT-002",
        )
        db_session.add(person)
        db_session.commit()

        importer.client.get_all.return_value = iter(
            [
                {
                    "name": "CONTACT-002",
                    "first_name": "Jane",
                    "last_name": "Existing",
                    "email_id": email,
                }
            ]
        )

        stats = importer._import_contacts(db_session)
        # Should link to existing, not create new
        assert stats.created == 0
        assert stats.skipped == 1

    def test_reimport_is_idempotent(self, db_session, importer, connector_config):
        """Second import of same contact doesn't create duplicate."""
        email = f"idem-{uuid.uuid4().hex[:8]}@example.com"
        importer.client.get_all.return_value = iter(
            [
                {
                    "name": "CONTACT-003",
                    "first_name": "Idem",
                    "last_name": "Test",
                    "email_id": email,
                }
            ]
        )
        stats1 = importer._import_contacts(db_session)
        assert stats1.created == 1

        # Second import — same data
        importer.client.get_all.return_value = iter(
            [
                {
                    "name": "CONTACT-003",
                    "first_name": "Idem",
                    "last_name": "Updated",
                    "email_id": email,
                }
            ]
        )
        stats2 = importer._import_contacts(db_session)
        assert stats2.created == 0
        assert stats2.updated == 1

        # Only one person with this erpnext_id
        count = db_session.query(Person).filter(Person.erpnext_id == "CONTACT-003").count()
        assert count == 1


class TestImportCustomers:
    def test_new_customer_sets_erpnext_id(self, db_session, importer):
        importer.client.get_all.return_value = iter([{"name": "CUST-001"}])
        importer.client.get_doc.return_value = {
            "name": "CUST-001",
            "customer_name": "Acme Corp",
            "customer_type": "Company",
        }

        stats = importer._import_customers(db_session)
        assert stats.created == 1

        org = db_session.query(Organization).filter(Organization.erpnext_id == "CUST-001").first()
        assert org is not None
        assert org.name == "Acme Corp"
        assert org.erpnext_id == "CUST-001"

    def test_splynx_id_captured(self, db_session, importer):
        importer.client.get_all.return_value = iter([{"name": "CUST-SPLYNX"}])
        importer.client.get_doc.return_value = {
            "name": "CUST-SPLYNX",
            "customer_name": "Splynx Customer",
            "custom_splynx_id": "42",
        }

        stats = importer._import_customers(db_session)
        assert stats.created == 1

        # Find subscriber by organization erpnext_id
        org = db_session.query(Organization).filter(Organization.erpnext_id == "CUST-SPLYNX").first()
        sub = db_session.query(Subscriber).filter(Subscriber.organization_id == org.id).first()
        assert sub is not None
        assert sub.sync_metadata is not None
        assert sub.sync_metadata.get("erpnext_splynx_id") == "42"


class TestImportProjects:
    def test_new_project_sets_erpnext_id_and_number(self, db_session, importer):
        importer.client.get_all.return_value = iter([{"name": "PROJ-001"}])
        importer.client.get_doc.return_value = {
            "name": "PROJ-001",
            "project_name": "Fiber Phase 1",
            "status": "Open",
            "priority": "Medium",
            "comments": [],
        }

        with patch("app.services.numbering.generate_number", return_value="PROJ-0001"):
            stats, _comment_stats = importer._import_projects(db_session)

        assert stats.created == 1

        project = db_session.query(Project).filter(Project.erpnext_id == "PROJ-001").first()
        assert project is not None
        assert project.name == "Fiber Phase 1"
        assert project.erpnext_id == "PROJ-001"
        assert project.number == "PROJ-0001"

    def test_project_comments_imported(self, db_session, importer):
        importer.client.get_all.return_value = iter([{"name": "PROJ-002"}])
        importer.client.get_doc.return_value = {
            "name": "PROJ-002",
            "project_name": "Fiber Phase 2",
            "status": "Open",
            "comments": [
                {
                    "name": "pc-001",
                    "comment": "Kickoff meeting done",
                    "comment_type": "Comment",
                    "comment_by": "pm@test.com",
                },
                {
                    "name": "pc-002",
                    "comment": "Status changed to Open",
                    "comment_type": "Info",
                    "comment_by": "system",
                },
            ],
        }

        with patch("app.services.numbering.generate_number", return_value="PROJ-0002"):
            stats, comment_stats = importer._import_projects(db_session)

        assert stats.created == 1
        assert comment_stats.created == 2

        project = db_session.query(Project).filter(Project.erpnext_id == "PROJ-002").first()
        comments = db_session.query(ProjectComment).filter(ProjectComment.project_id == project.id).all()
        assert len(comments) == 2
        assert any("Kickoff" in c.body for c in comments)

    def test_project_comments_idempotent(self, db_session, importer):
        """Re-importing same project doesn't duplicate comments."""
        importer.client.get_all.return_value = iter([{"name": "PROJ-003"}])
        doc = {
            "name": "PROJ-003",
            "project_name": "Fiber Phase 3",
            "status": "Open",
            "comments": [
                {
                    "name": "pc-100",
                    "comment": "First comment",
                    "comment_type": "Comment",
                },
            ],
        }
        importer.client.get_doc.return_value = doc

        with patch("app.services.numbering.generate_number", return_value="PROJ-0003"):
            _, comment_stats1 = importer._import_projects(db_session)
        assert comment_stats1.created == 1

        # Re-import — same doc
        importer.client.get_all.return_value = iter([{"name": "PROJ-003"}])
        importer.client.get_doc.return_value = doc
        _, comment_stats2 = importer._import_projects(db_session)
        assert comment_stats2.created == 0
        assert comment_stats2.skipped == 1

        # Only 1 comment exists
        project = db_session.query(Project).filter(Project.erpnext_id == "PROJ-003").first()
        count = db_session.query(ProjectComment).filter(ProjectComment.project_id == project.id).count()
        assert count == 1


class TestImportTasks:
    def test_new_task_sets_erpnext_id_and_number(self, db_session, importer, connector_config):
        # Create a project first
        project = Project(name="Test Project", erpnext_id="PROJ-FOR-TASK")
        db_session.add(project)
        db_session.flush()

        # Create ExternalReference for the project
        ref = ExternalReference(
            connector_config_id=connector_config.id,
            entity_type=ExternalEntityType.project,
            external_id="PROJ-FOR-TASK",
            entity_id=project.id,
        )
        db_session.add(ref)
        db_session.commit()

        importer.client.get_all.return_value = iter(
            [{"name": "TASK-001", "project": "PROJ-FOR-TASK"}]
        )
        importer.client.get_doc.return_value = {
            "name": "TASK-001",
            "subject": "Splice segment A",
            "status": "Open",
            "priority": "Medium",
            "project": "PROJ-FOR-TASK",
            "comments": [],
        }

        with patch("app.services.numbering.generate_number", return_value="TK-0001"):
            stats, _comment_stats = importer._import_tasks(db_session)

        assert stats.created == 1

        task = db_session.query(ProjectTask).filter(ProjectTask.erpnext_id == "TASK-001").first()
        assert task is not None
        assert task.title == "Splice segment A"
        assert task.erpnext_id == "TASK-001"
        assert task.number == "TK-0001"
        assert task.project_id == project.id

    def test_task_without_project_skipped(self, db_session, importer):
        importer.client.get_all.return_value = iter(
            [{"name": "TASK-ORPHAN", "project": None}]
        )

        with patch("app.services.numbering.generate_number", return_value="TK-0002"):
            stats, _ = importer._import_tasks(db_session)

        assert stats.skipped == 1
        assert stats.created == 0

    def test_task_comments_imported(self, db_session, importer, connector_config):
        # Setup project
        project = Project(name="Task Comment Proj", erpnext_id="PROJ-TC")
        db_session.add(project)
        db_session.flush()
        ref = ExternalReference(
            connector_config_id=connector_config.id,
            entity_type=ExternalEntityType.project,
            external_id="PROJ-TC",
            entity_id=project.id,
        )
        db_session.add(ref)
        db_session.commit()

        importer.client.get_all.return_value = iter(
            [{"name": "TASK-TC-001", "project": "PROJ-TC"}]
        )
        importer.client.get_doc.return_value = {
            "name": "TASK-TC-001",
            "subject": "Task with comments",
            "status": "Working",
            "project": "PROJ-TC",
            "comments": [
                {
                    "name": "tc-001",
                    "comment": "Started work",
                    "comment_type": "Comment",
                },
            ],
        }

        with patch("app.services.numbering.generate_number", return_value="TK-0003"):
            stats, comment_stats = importer._import_tasks(db_session)

        assert stats.created == 1
        assert comment_stats.created == 1

        task = db_session.query(ProjectTask).filter(ProjectTask.erpnext_id == "TASK-TC-001").first()
        comments = db_session.query(ProjectTaskComment).filter(ProjectTaskComment.task_id == task.id).all()
        assert len(comments) == 1
        assert comments[0].body == "Started work"


class TestImportTickets:
    def test_new_ticket_sets_erpnext_id_and_number(self, db_session, importer):
        importer.client.get_all.side_effect = [
            # First call: HD Ticket stubs
            iter([{"name": "1"}]),
            # Second call: Communications (empty)
            iter([]),
        ]
        importer.client.get_doc.return_value = {
            "name": "1",
            "subject": "Internet down",
            "status": "Open",
            "priority": "High",
            "comments": [],
        }

        with patch("app.services.numbering.generate_number", return_value="TKT-0001"):
            stats, _comment_stats = importer._import_tickets(db_session)

        assert stats.created == 1

        ticket = db_session.query(Ticket).filter(Ticket.erpnext_id == "1").first()
        assert ticket is not None
        assert ticket.title == "Internet down"
        assert ticket.erpnext_id == "1"
        assert ticket.number == "TKT-0001"

    def test_ticket_comments_imported(self, db_session, importer):
        importer.client.get_all.side_effect = [
            iter([{"name": "2"}]),
            iter([]),  # No communications
        ]
        importer.client.get_doc.return_value = {
            "name": "2",
            "subject": "Slow speeds",
            "status": "Open",
            "priority": "Medium",
            "comments": [
                {
                    "name": "tc-001",
                    "comment": "Checked line stats",
                    "comment_type": "Comment",
                },
                {
                    "name": "tc-002",
                    "comment": "Escalated to NOC",
                    "comment_type": "Info",
                },
            ],
        }

        with patch("app.services.numbering.generate_number", return_value="TKT-0002"):
            stats, comment_stats = importer._import_tickets(db_session)

        assert stats.created == 1
        assert comment_stats.created == 2

        ticket = db_session.query(Ticket).filter(Ticket.erpnext_id == "2").first()
        comments = db_session.query(TicketComment).filter(TicketComment.ticket_id == ticket.id).all()
        assert len(comments) == 2

    def test_ticket_communications_imported(self, db_session, importer):
        importer.client.get_all.side_effect = [
            iter([{"name": "3"}]),
            # Communications for ticket "3"
            iter(
                [
                    {
                        "name": "COMM-100",
                        "subject": "Re: Fiber issue",
                        "content": "<p>Thanks for the update</p>",
                        "sender": "customer@test.com",
                        "sent_or_received": "Received",
                        "creation": "2024-01-15",
                    },
                ]
            ),
        ]
        importer.client.get_doc.return_value = {
            "name": "3",
            "subject": "Fiber cut",
            "status": "Open",
            "priority": "Urgent",
            "comments": [],
        }

        with patch("app.services.numbering.generate_number", return_value="TKT-0003"):
            stats, comment_stats = importer._import_tickets(db_session)

        assert stats.created == 1
        assert comment_stats.created == 1

        ticket = db_session.query(Ticket).filter(Ticket.erpnext_id == "3").first()
        comments = db_session.query(TicketComment).filter(TicketComment.ticket_id == ticket.id).all()
        assert len(comments) == 1
        assert "Fiber issue" in comments[0].body

    def test_comm_id_prefix_avoids_collision(self, db_session, importer, connector_config):
        """Communication external IDs use 'comm-' prefix to avoid collision with child comments."""
        importer.client.get_all.side_effect = [
            iter([{"name": "4"}]),
            iter(
                [
                    {
                        "name": "row-001",  # Same ID as a potential child comment
                        "subject": "Email thread",
                        "content": "Email body",
                        "sender": "a@test.com",
                        "sent_or_received": "Received",
                    },
                ]
            ),
        ]
        importer.client.get_doc.return_value = {
            "name": "4",
            "subject": "Test collision",
            "status": "Open",
            "priority": "Low",
            "comments": [
                {
                    "name": "row-001",  # Same raw ID as the communication
                    "comment": "A child comment",
                    "comment_type": "Comment",
                },
            ],
        }

        with patch("app.services.numbering.generate_number", return_value="TKT-0004"):
            _stats, comment_stats = importer._import_tickets(db_session)

        # Both should be created — no collision
        assert comment_stats.created == 2

        # Verify ExternalReferences use different keys
        refs = (
            db_session.query(ExternalReference)
            .filter(ExternalReference.connector_config_id == connector_config.id)
            .filter(ExternalReference.entity_type == ExternalEntityType.ticket_comment)
            .all()
        )
        ref_ids = {r.external_id for r in refs}
        assert "row-001" in ref_ids  # child comment
        assert "comm-row-001" in ref_ids  # communication with prefix

    def test_reimport_tickets_idempotent(self, db_session, importer):
        """Re-importing same ticket doesn't duplicate."""
        importer.client.get_all.side_effect = [
            iter([{"name": "5"}]),
            iter([]),
        ]
        importer.client.get_doc.return_value = {
            "name": "5",
            "subject": "Idem ticket",
            "status": "Open",
            "priority": "Medium",
            "comments": [],
        }

        with patch("app.services.numbering.generate_number", return_value="TKT-0005"):
            stats1, _ = importer._import_tickets(db_session)
        assert stats1.created == 1

        # Re-import
        importer.client.get_all.side_effect = [
            iter([{"name": "5"}]),
            iter([]),
        ]
        importer.client.get_doc.return_value = {
            "name": "5",
            "subject": "Idem ticket updated",
            "status": "Resolved",
            "priority": "Medium",
            "comments": [],
        }

        stats2, _ = importer._import_tickets(db_session)
        assert stats2.created == 0
        assert stats2.updated == 1

        count = db_session.query(Ticket).filter(Ticket.erpnext_id == "5").count()
        assert count == 1


class TestResolveSubscriberId:
    def test_resolve_by_external_reference(self, db_session, importer, connector_config):
        """Resolves subscriber via ExternalReference (connector-scoped)."""
        sub = Subscriber(subscriber_number="SUB-001", is_active=True)
        db_session.add(sub)
        db_session.flush()

        ref = ExternalReference(
            connector_config_id=connector_config.id,
            entity_type=ExternalEntityType.subscriber,
            external_id="CUST-LOOKUP",
            entity_id=sub.id,
        )
        db_session.add(ref)
        db_session.commit()

        result = importer._resolve_subscriber_id(db_session, "CUST-LOOKUP")
        assert result == sub.id

    def test_resolve_by_organization_erpnext_id(self, db_session, importer):
        """Resolves via Organization.erpnext_id -> first Subscriber."""
        org = Organization(name="Fallback Org", erpnext_id="CUST-FALLBACK")
        db_session.add(org)
        db_session.flush()

        sub = Subscriber(organization_id=org.id, subscriber_number="SUB-FB", is_active=True)
        db_session.add(sub)
        db_session.commit()

        result = importer._resolve_subscriber_id(db_session, "CUST-FALLBACK")
        assert result == sub.id

    def test_resolve_returns_none_if_not_found(self, db_session, importer):
        result = importer._resolve_subscriber_id(db_session, "NONEXISTENT")
        assert result is None


# ---------------------------------------------------------------------------
# DotMac ERP Sync Payload Tests
# ---------------------------------------------------------------------------


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


class TestImportResult:
    def test_to_dict_includes_comment_stats(self):
        result = ImportResult(success=True)
        result.ticket_comments = ImportStats(created=3, skipped=1)
        result.project_comments = ImportStats(created=5)
        result.task_comments = ImportStats(created=2)

        d = result.to_dict()
        assert d["ticket_comments"]["created"] == 3
        assert d["ticket_comments"]["skipped"] == 1
        assert d["project_comments"]["created"] == 5
        assert d["task_comments"]["created"] == 2

    def test_import_stats_error_messages_limited(self):
        stats = ImportStats(errors=15)
        stats.error_messages = [f"Error {i}" for i in range(15)]
        d = stats.to_dict()
        assert len(d["error_messages"]) == 10  # Limited to 10
