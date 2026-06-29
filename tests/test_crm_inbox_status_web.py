from unittest.mock import Mock, patch

from app.services.crm.inbox.resolve_gate import GateCheckResult
from app.web.admin.crm_inbox_status import _render_thread_or_error, update_conversation_status


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
    assert "Sent to ticket" in body
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
    assert "Sent to ticket" not in body


def test_thread_rerender_includes_insert_introduction_context():
    db = Mock()
    request = Mock()
    current_user = {"person_id": "person-1"}
    captured_context = {}

    def capture_template(_template_name, context):
        captured_context.update(context)
        return Mock()

    with (
        patch(
            "app.services.crm.inbox.thread.load_conversation_thread",
            return_value=Mock(kind="success", conversation=Mock(), messages=[]),
        ),
        patch("app.web.admin.crm_inbox_status.format_conversation_for_template", return_value={"id": "conv-1"}),
        patch("app.web.admin.crm_inbox_status.get_conversation_csat_event", return_value=None),
        patch("app.web.admin.crm_inbox_status._get_current_roles", return_value=[]),
        patch("app.web.admin.crm_inbox_status.filter_messages_for_user", return_value=[]),
        patch("app.web.admin.crm_inbox_status._load_talk_escalation_recipients", return_value=[]),
        patch("app.services.crm.inbox.templates.message_templates.list", return_value=[]),
        patch("app.services.crm.inbox.agents.list_active_agents_for_mentions", return_value=[]),
        patch("app.services.crm.inbox.agent_introduction.get_introduction_template", return_value="Hi {agent_name}."),
        patch("app.services.crm.inbox.agent_introduction.render_introduction_template", return_value="Hi Ada."),
        patch("app.web.admin.crm_inbox_status.templates.TemplateResponse", side_effect=capture_template),
    ):
        _render_thread_or_error(request, db, "conv-1", current_user)

    assert captured_context["introduction_template"] == "Hi {agent_name}."
    assert captured_context["rendered_introduction_template"] == "Hi Ada."
    assert captured_context["message_templates"] == []
    assert captured_context["mention_agents"] == []
