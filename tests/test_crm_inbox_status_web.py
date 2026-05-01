from unittest.mock import Mock, patch

from app.services.crm.inbox.resolve_gate import GateCheckResult
from app.web.admin.crm_inbox_status import update_conversation_status


def _run_to_completion(coro):
    try:
        coro.send(None)
    except StopIteration as exc:
        return exc.value
    raise AssertionError("Expected coroutine to complete without awaiting")


def test_resolve_gate_shows_ticket_handoff_option_when_ticket_exists():
    db = Mock()

    with (
        patch("app.web.admin.crm_inbox_status.get_csrf_token", return_value="csrf"),
        patch("app.web.admin._auth_helpers.get_current_user", return_value={"person_id": "person-1"}),
        patch("app.services.crm.inbox.resolve_gate.check_resolve_gate", return_value=GateCheckResult(kind="no_gate")),
        patch(
            "app.web.admin.crm_inbox_status._get_conversation_ticket_context",
            return_value={"reference": "TCK-1001", "href": "/admin/support/tickets/TCK-1001"},
        ),
    ):
        request = type(
            "Req",
            (),
            {
                "headers": {"HX-Target": "message-thread"},
                "query_params": {"skip_tag_check": "1"},
            },
        )()
        response = _run_to_completion(
            update_conversation_status(
                request,
                conversation_id="conv-1",
                new_status="resolved",
                db=db,
            )
        )

    body = response.body.decode()
    assert "Resolved with Ticket Handoff" in body
    assert "TCK-1001" in body


def test_resolve_gate_hides_ticket_handoff_option_without_linked_ticket():
    db = Mock()

    with (
        patch("app.web.admin.crm_inbox_status.get_csrf_token", return_value="csrf"),
        patch("app.web.admin._auth_helpers.get_current_user", return_value={"person_id": "person-1"}),
        patch(
            "app.services.crm.inbox.resolve_gate.check_resolve_gate",
            return_value=GateCheckResult(kind="needs_gate"),
        ),
        patch("app.web.admin.crm_inbox_status._get_conversation_ticket_context", return_value=None),
    ):
        request = type(
            "Req",
            (),
            {
                "headers": {"HX-Target": "message-thread"},
                "query_params": {"skip_tag_check": "1"},
            },
        )()
        response = _run_to_completion(
            update_conversation_status(
                request,
                conversation_id="conv-1",
                new_status="resolved",
                db=db,
            )
        )

    body = response.body.decode()
    assert "Resolve Without Lead" in body
    assert "Resolved with Ticket Handoff" not in body
