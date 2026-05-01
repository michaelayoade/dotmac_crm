from unittest.mock import Mock, patch

import pytest
from starlette.requests import Request

from app.services.crm.inbox.resolve_gate import GateCheckResult
from app.web.admin.crm_inbox_status import update_conversation_status


def _hx_request(*, query_string: str = "") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/admin/crm/inbox/conversation/conv-1/status",
        "query_string": query_string.encode(),
        "headers": [(b"hx-target", b"message-thread")],
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_resolve_gate_shows_ticket_handoff_option_when_ticket_exists():
    request = _hx_request(query_string="skip_tag_check=1")
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
        response = await update_conversation_status(
            request,
            conversation_id="conv-1",
            new_status="resolved",
            db=db,
        )

    body = response.body.decode()
    assert "Resolved with Ticket Handoff" in body
    assert "TCK-1001" in body


@pytest.mark.asyncio
async def test_resolve_gate_hides_ticket_handoff_option_without_linked_ticket():
    request = _hx_request(query_string="skip_tag_check=1")
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
        response = await update_conversation_status(
            request,
            conversation_id="conv-1",
            new_status="resolved",
            db=db,
        )

    body = response.body.decode()
    assert "Resolve Without Lead" in body
    assert "Resolved with Ticket Handoff" not in body
