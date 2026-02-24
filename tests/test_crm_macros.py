"""Tests for CRM conversation macros CRUD and executor."""

import json
import uuid
from unittest.mock import patch

import pytest

from app.models.crm.enums import MacroVisibility
from app.models.crm.team import CrmAgent
from app.models.person import Person


@pytest.fixture()
def macro_agent(db_session):
    """Agent for macro tests."""
    person = Person(
        first_name="Macro",
        last_name="Tester",
        email=f"macro-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)

    agent = CrmAgent(
        person_id=person.id,
        title="Macro Agent",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    return agent


@pytest.fixture()
def second_agent(db_session):
    """Second agent for visibility tests."""
    person = Person(
        first_name="Other",
        last_name="Agent",
        email=f"other-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)

    agent = CrmAgent(
        person_id=person.id,
        title="Other Agent",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    return agent


def _sample_actions(action_type="set_status", **params):
    """Build a minimal valid actions list."""
    return [{"action_type": action_type, "params": params}]


# ── CRUD Tests ──────────────────────────────────────────────


class TestMacroCRUD:
    def test_create_macro(self, db_session, macro_agent):
        from app.services.crm.inbox.macros import conversation_macros

        actions = _sample_actions("set_status", status="resolved")
        macro = conversation_macros.create(
            db_session,
            name="Resolve conversation",
            description="Mark as resolved",
            visibility=MacroVisibility.personal,
            actions=actions,
            created_by_agent_id=str(macro_agent.id),
        )
        assert macro.id is not None
        assert macro.name == "Resolve conversation"
        assert macro.visibility == MacroVisibility.personal
        assert len(macro.actions) == 1
        assert macro.is_active is True
        assert macro.execution_count == 0

    def test_get_macro(self, db_session, macro_agent):
        from app.services.crm.inbox.macros import conversation_macros

        macro = conversation_macros.create(
            db_session,
            name="Test Get",
            description=None,
            visibility=MacroVisibility.personal,
            actions=_sample_actions("set_status", status="open"),
            created_by_agent_id=str(macro_agent.id),
        )
        fetched = conversation_macros.get(db_session, str(macro.id))
        assert fetched.id == macro.id
        assert fetched.name == "Test Get"

    def test_get_macro_not_found(self, db_session):
        from fastapi import HTTPException

        from app.services.crm.inbox.macros import conversation_macros

        with pytest.raises(HTTPException) as exc_info:
            conversation_macros.get(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404

    def test_update_macro(self, db_session, macro_agent):
        from app.services.crm.inbox.macros import conversation_macros

        macro = conversation_macros.create(
            db_session,
            name="Original",
            description=None,
            visibility=MacroVisibility.personal,
            actions=_sample_actions("set_status", status="open"),
            created_by_agent_id=str(macro_agent.id),
        )
        updated = conversation_macros.update(
            db_session,
            str(macro.id),
            name="Updated Name",
            visibility=MacroVisibility.shared,
        )
        assert updated.name == "Updated Name"
        assert updated.visibility == MacroVisibility.shared

    def test_delete_macro_soft(self, db_session, macro_agent):
        from app.services.crm.inbox.macros import conversation_macros

        macro = conversation_macros.create(
            db_session,
            name="To Delete",
            description=None,
            visibility=MacroVisibility.personal,
            actions=_sample_actions("set_status", status="resolved"),
            created_by_agent_id=str(macro_agent.id),
        )
        conversation_macros.delete(db_session, str(macro.id))
        db_session.refresh(macro)
        assert macro.is_active is False

    def test_list_macros(self, db_session, macro_agent):
        from app.services.crm.inbox.macros import conversation_macros

        for i in range(3):
            conversation_macros.create(
                db_session,
                name=f"Macro {i}",
                description=None,
                visibility=MacroVisibility.personal,
                actions=_sample_actions("set_status", status="open"),
                created_by_agent_id=str(macro_agent.id),
            )
        result = conversation_macros.list(db_session, agent_id=str(macro_agent.id))
        assert len(result) == 3

    def test_list_for_agent_includes_shared(self, db_session, macro_agent, second_agent):
        from app.services.crm.inbox.macros import conversation_macros

        # Personal macro by macro_agent
        conversation_macros.create(
            db_session,
            name="My Personal",
            description=None,
            visibility=MacroVisibility.personal,
            actions=_sample_actions("set_status", status="open"),
            created_by_agent_id=str(macro_agent.id),
        )
        # Shared macro by second_agent
        conversation_macros.create(
            db_session,
            name="Shared Macro",
            description=None,
            visibility=MacroVisibility.shared,
            actions=_sample_actions("set_status", status="resolved"),
            created_by_agent_id=str(second_agent.id),
        )
        # Personal macro by second_agent (should NOT appear)
        conversation_macros.create(
            db_session,
            name="Others Personal",
            description=None,
            visibility=MacroVisibility.personal,
            actions=_sample_actions("add_tag", tag="test"),
            created_by_agent_id=str(second_agent.id),
        )

        result = conversation_macros.list_for_agent(db_session, str(macro_agent.id))
        names = {m.name for m in result}
        assert "My Personal" in names
        assert "Shared Macro" in names
        assert "Others Personal" not in names

    def test_create_macro_invalid_action_type(self, db_session, macro_agent):
        from app.services.crm.inbox.macros import conversation_macros

        with pytest.raises(ValueError, match="Invalid action type"):
            conversation_macros.create(
                db_session,
                name="Bad Action",
                description=None,
                visibility=MacroVisibility.personal,
                actions=[{"action_type": "invalid_action", "params": {}}],
                created_by_agent_id=str(macro_agent.id),
            )


# ── Executor Tests ──────────────────────────────────────────


class TestMacroExecutor:
    def _create_macro(self, db_session, macro_agent, actions):
        from app.services.crm.inbox.macros import conversation_macros

        return conversation_macros.create(
            db_session,
            name="Test Executor Macro",
            description=None,
            visibility=MacroVisibility.personal,
            actions=actions,
            created_by_agent_id=str(macro_agent.id),
        )

    @patch("app.services.crm.inbox.macro_executor.log_conversation_action")
    @patch("app.services.crm.inbox.macro_executor._exec_set_status")
    def test_execute_single_action(self, mock_set_status, mock_log, db_session, macro_agent):
        from app.services.crm.inbox.macro_executor import execute_macro

        mock_set_status.return_value = {"action": "set_status", "ok": True}
        actions = _sample_actions("set_status", status="resolved")
        macro = self._create_macro(db_session, macro_agent, actions)

        result = execute_macro(
            db_session,
            macro_id=str(macro.id),
            conversation_id=str(uuid.uuid4()),
            actions=actions,
            actor_agent_id=str(macro_agent.id),
        )
        assert result.ok is True
        assert result.actions_executed == 1
        assert result.actions_failed == 0

    @patch("app.services.crm.inbox.macro_executor.log_conversation_action")
    @patch("app.services.crm.inbox.macro_executor._exec_set_status")
    @patch("app.services.crm.inbox.macro_executor._exec_add_tag")
    def test_execute_multiple_actions(self, mock_tag, mock_status, mock_log, db_session, macro_agent):
        from app.services.crm.inbox.macro_executor import execute_macro

        mock_status.return_value = {"action": "set_status", "ok": True}
        mock_tag.return_value = {"action": "add_tag", "ok": True}
        actions = [
            {"action_type": "set_status", "params": {"status": "resolved"}},
            {"action_type": "add_tag", "params": {"tag": "vip"}},
        ]
        macro = self._create_macro(db_session, macro_agent, actions)

        result = execute_macro(
            db_session,
            macro_id=str(macro.id),
            conversation_id=str(uuid.uuid4()),
            actions=actions,
            actor_agent_id=str(macro_agent.id),
        )
        assert result.ok is True
        assert result.actions_executed == 2
        assert result.actions_failed == 0

    @patch("app.services.crm.inbox.macro_executor.log_conversation_action")
    @patch("app.services.crm.inbox.macro_executor._exec_set_status")
    @patch("app.services.crm.inbox.macro_executor._exec_add_tag")
    def test_partial_failure(self, mock_tag, mock_status, mock_log, db_session, macro_agent):
        from app.services.crm.inbox.macro_executor import execute_macro

        mock_status.side_effect = ValueError("Status update failed")
        mock_tag.return_value = {"action": "add_tag", "ok": True}
        actions = [
            {"action_type": "set_status", "params": {"status": "resolved"}},
            {"action_type": "add_tag", "params": {"tag": "vip"}},
        ]
        macro = self._create_macro(db_session, macro_agent, actions)

        result = execute_macro(
            db_session,
            macro_id=str(macro.id),
            conversation_id=str(uuid.uuid4()),
            actions=actions,
            actor_agent_id=str(macro_agent.id),
        )
        assert result.ok is False
        assert result.actions_executed == 1
        assert result.actions_failed == 1
        assert "1 action(s) failed" in result.error_detail

    @patch("app.services.crm.inbox.macro_executor.log_conversation_action")
    @patch("app.services.crm.inbox.macro_executor._exec_set_status")
    def test_execution_increments_counter(self, mock_status, mock_log, db_session, macro_agent):
        from app.services.crm.inbox.macro_executor import execute_macro

        mock_status.return_value = {"action": "set_status", "ok": True}
        actions = _sample_actions("set_status", status="resolved")
        macro = self._create_macro(db_session, macro_agent, actions)
        assert macro.execution_count == 0

        execute_macro(
            db_session,
            macro_id=str(macro.id),
            conversation_id=str(uuid.uuid4()),
            actions=actions,
            actor_agent_id=str(macro_agent.id),
        )
        db_session.refresh(macro)
        assert macro.execution_count == 1


# ── Settings Admin Tests ────────────────────────────────────


class TestMacroSettingsAdmin:
    def test_create_macro_via_admin(self, db_session, macro_agent):
        from app.services.crm.inbox.settings_admin import create_macro

        actions = json.dumps(_sample_actions("set_status", status="resolved"))
        result = create_macro(
            db_session,
            name="Admin Created",
            description="Test macro",
            visibility="personal",
            actions_json=actions,
            created_by_agent_id=str(macro_agent.id),
        )
        assert result.ok is True

    def test_create_macro_empty_actions(self, db_session, macro_agent):
        from app.services.crm.inbox.settings_admin import create_macro

        result = create_macro(
            db_session,
            name="Empty",
            description=None,
            visibility="personal",
            actions_json="[]",
            created_by_agent_id=str(macro_agent.id),
        )
        assert result.ok is False
        assert "At least one action" in result.error_detail

    def test_delete_macro_via_admin(self, db_session, macro_agent):
        from app.services.crm.inbox.macros import conversation_macros
        from app.services.crm.inbox.settings_admin import delete_macro

        macro = conversation_macros.create(
            db_session,
            name="To Delete Admin",
            description=None,
            visibility=MacroVisibility.personal,
            actions=_sample_actions("set_status", status="resolved"),
            created_by_agent_id=str(macro_agent.id),
        )
        result = delete_macro(
            db_session,
            macro_id=str(macro.id),
            actor_agent_id=str(macro_agent.id),
        )
        assert result.ok is True
        db_session.refresh(macro)
        assert macro.is_active is False


# ── Permission Tests ────────────────────────────────────────


class TestMacroPermissions:
    def test_can_use_macros_admin(self):
        from app.services.crm.inbox.permissions import can_use_macros

        assert can_use_macros(roles=["admin"]) is True

    def test_can_use_macros_with_scope(self):
        from app.services.crm.inbox.permissions import can_use_macros

        assert can_use_macros(scopes=["crm:macro:read"]) is True

    def test_can_use_macros_no_scope(self):
        from app.services.crm.inbox.permissions import can_use_macros

        assert can_use_macros(roles=["viewer"], scopes=["reports:read"]) is False

    def test_can_manage_macros_admin(self):
        from app.services.crm.inbox.permissions import can_manage_macros

        assert can_manage_macros(roles=["admin"]) is True

    def test_can_manage_macros_with_scope(self):
        from app.services.crm.inbox.permissions import can_manage_macros

        assert can_manage_macros(scopes=["crm:macro:write"]) is True

    def test_can_manage_macros_no_scope(self):
        from app.services.crm.inbox.permissions import can_manage_macros

        assert can_manage_macros(roles=["viewer"], scopes=["crm:macro:read"]) is False


# ── Validate Actions Tests ──────────────────────────────────


class TestValidateActions:
    def test_valid_actions(self):
        from app.services.crm.inbox.macros import _validate_actions

        _validate_actions(
            [
                {"action_type": "set_status", "params": {"status": "resolved"}},
                {"action_type": "add_tag", "params": {"tag": "vip"}},
                {"action_type": "assign_conversation", "params": {"agent_id": str(uuid.uuid4())}},
                {"action_type": "send_template", "params": {"template_id": str(uuid.uuid4())}},
            ]
        )

    def test_invalid_action_type(self):
        from app.services.crm.inbox.macros import _validate_actions

        with pytest.raises(ValueError, match="Invalid action type"):
            _validate_actions([{"action_type": "nope", "params": {}}])

    def test_missing_params(self):
        from app.services.crm.inbox.macros import _validate_actions

        with pytest.raises(ValueError, match="Missing params"):
            _validate_actions([{"action_type": "set_status"}])
