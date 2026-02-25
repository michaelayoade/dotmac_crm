"""Tests for ticket auto-assignment integration in ticket creation."""

from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

from app.models.person import Person
from app.schemas.tickets import TicketCreate
from app.services import tickets as ticket_service
from app.services.ticket_assignment.engine import AssignmentResult


def _person(db_session) -> Person:
    person = Person(
        first_name="Auto",
        last_name="Assignee",
        email=f"auto-{uuid4().hex[:8]}@example.com",
    )
    db_session.add(person)
    db_session.flush()
    return person


def test_ticket_create_calls_auto_assignment_when_enabled(db_session, monkeypatch):
    monkeypatch.setattr(ticket_service, "generate_number", lambda **_: None)
    monkeypatch.setattr(ticket_service, "emit_event", lambda *_, **__: None)
    monkeypatch.setattr(ticket_service, "_notify_ticket_role_assignment_in_app", lambda *_, **__: set())
    monkeypatch.setattr(ticket_service, "_notify_ticket_service_team_assignment", lambda *_, **__: set())

    def _resolve_value(_db, domain, key):
        if domain.value == "workflow" and key == "ticket_auto_assignment_enabled":
            return True
        return None

    monkeypatch.setattr(ticket_service.settings_spec, "resolve_value", _resolve_value)

    mock_assign = Mock(
        return_value=AssignmentResult(
            assigned=True,
            ticket_id="unused",
            rule_id="rule-1",
            assignee_person_id="person-1",
            reason="assigned",
        )
    )
    mock_audit = Mock()
    monkeypatch.setattr("app.services.ticket_assignment.auto_assign_ticket", mock_assign)
    monkeypatch.setattr("app.services.audit_helpers.log_audit_event", mock_audit)

    payload = TicketCreate(title="Needs auto-assignment")
    ticket = ticket_service.Tickets.create(db_session, payload)

    assert ticket is not None
    mock_assign.assert_called_once()
    _, kwargs = mock_assign.call_args
    assert kwargs["trigger"] == "create"
    mock_audit.assert_called_once()


def test_ticket_create_does_not_call_auto_assignment_when_disabled(db_session, monkeypatch):
    monkeypatch.setattr(ticket_service, "generate_number", lambda **_: None)
    monkeypatch.setattr(ticket_service, "emit_event", lambda *_, **__: None)
    monkeypatch.setattr(ticket_service, "_notify_ticket_role_assignment_in_app", lambda *_, **__: set())
    monkeypatch.setattr(ticket_service, "_notify_ticket_service_team_assignment", lambda *_, **__: set())
    monkeypatch.setattr(ticket_service.settings_spec, "resolve_value", lambda *_: False)

    mock_assign = Mock()
    monkeypatch.setattr("app.services.ticket_assignment.auto_assign_ticket", mock_assign)

    payload = TicketCreate(title="No auto-assignment")
    ticket = ticket_service.Tickets.create(db_session, payload)

    assert ticket is not None
    mock_assign.assert_not_called()


def test_ticket_create_does_not_call_auto_assignment_when_already_assigned(db_session, monkeypatch):
    monkeypatch.setattr(ticket_service, "generate_number", lambda **_: None)
    monkeypatch.setattr(ticket_service, "emit_event", lambda *_, **__: None)
    monkeypatch.setattr(ticket_service, "_notify_ticket_role_assignment_in_app", lambda *_, **__: set())
    monkeypatch.setattr(ticket_service, "_notify_ticket_service_team_assignment", lambda *_, **__: set())
    monkeypatch.setattr(ticket_service.settings_spec, "resolve_value", lambda *_: True)

    assignee = _person(db_session)
    mock_assign = Mock()
    monkeypatch.setattr("app.services.ticket_assignment.auto_assign_ticket", mock_assign)

    payload = TicketCreate(
        title="Already assigned",
        assigned_to_person_id=assignee.id,
    )
    ticket = ticket_service.Tickets.create(db_session, payload)

    assert ticket is not None
    assert ticket.assigned_to_person_id == assignee.id
    mock_assign.assert_not_called()
