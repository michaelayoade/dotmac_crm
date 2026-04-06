"""Tests for chat widget dialog flow feature."""

import uuid

import pytest
from sqlalchemy.orm import Session

from app.schemas.crm.chat_widget import (
    ChatWidgetConfigCreate,
    DialogFlowOption,
    DialogFlowStep,
)
from app.services.crm.widget.service import (
    _validate_dialog_flow,
    apply_dialog_routing,
    widget_configs,
)

# --------------------------------------------------------------------------
# _validate_dialog_flow tests
# --------------------------------------------------------------------------


def test_validate_dialog_flow_disabled_skips():
    """When disabled, validation always passes."""
    _validate_dialog_flow(False, None)
    _validate_dialog_flow(False, [])


def test_validate_dialog_flow_enabled_no_steps():
    with pytest.raises(ValueError, match="requires at least one step"):
        _validate_dialog_flow(True, None)


def test_validate_dialog_flow_enabled_empty_steps():
    with pytest.raises(ValueError, match="requires at least one step"):
        _validate_dialog_flow(True, [])


def test_validate_dialog_flow_duplicate_ids():
    steps = [
        DialogFlowStep(id="a", type="terminal", message="Done"),
        DialogFlowStep(id="a", type="terminal", message="Also done"),
    ]
    with pytest.raises(ValueError, match="Duplicate dialog flow step ID: a"):
        _validate_dialog_flow(True, steps)


def test_validate_dialog_flow_no_terminal():
    steps = [
        DialogFlowStep(
            id="welcome",
            type="choice",
            message="Pick one",
            options=[DialogFlowOption(label="Sales", next_step="welcome")],
        ),
    ]
    with pytest.raises(ValueError, match="at least one terminal step"):
        _validate_dialog_flow(True, steps)


def test_validate_dialog_flow_orphan_next_step():
    steps = [
        DialogFlowStep(
            id="welcome",
            type="choice",
            message="Pick one",
            options=[DialogFlowOption(label="Sales", next_step="nonexistent")],
        ),
        DialogFlowStep(id="done", type="terminal", message="Done"),
    ]
    with pytest.raises(ValueError, match="references unknown step 'nonexistent'"):
        _validate_dialog_flow(True, steps)


def test_validate_dialog_flow_choice_no_options():
    steps = [
        DialogFlowStep(id="welcome", type="choice", message="Pick one", options=None),
        DialogFlowStep(id="done", type="terminal", message="Done"),
    ]
    with pytest.raises(ValueError, match="must have at least one option"):
        _validate_dialog_flow(True, steps)


def test_validate_dialog_flow_invalid_priority():
    steps = [
        DialogFlowStep(id="done", type="terminal", message="Done", priority="super_high"),
    ]
    with pytest.raises(ValueError, match="Invalid priority"):
        _validate_dialog_flow(True, steps)


def test_validate_dialog_flow_valid():
    """A valid dialog flow should pass without errors."""
    steps = [
        DialogFlowStep(
            id="welcome",
            type="choice",
            message="How can we help?",
            options=[
                DialogFlowOption(label="Sales", next_step="sales_done"),
                DialogFlowOption(label="Support", next_step="support_done"),
            ],
        ),
        DialogFlowStep(id="sales_done", type="terminal", message="Connecting to sales...", priority="medium"),
        DialogFlowStep(id="support_done", type="terminal", message="Connecting to support...", priority="high"),
    ]
    _validate_dialog_flow(True, steps)


# --------------------------------------------------------------------------
# Schema serialization tests
# --------------------------------------------------------------------------


def test_dialog_flow_step_serialization():
    step = DialogFlowStep(
        id="welcome",
        type="choice",
        message="Pick one",
        options=[DialogFlowOption(label="Sales", next_step="sales")],
    )
    data = step.model_dump(mode="json")
    assert data["id"] == "welcome"
    assert data["type"] == "choice"
    assert len(data["options"]) == 1
    assert data["options"][0]["label"] == "Sales"


def test_dialog_flow_terminal_step_serialization():
    step = DialogFlowStep(
        id="done",
        type="terminal",
        message="Connecting...",
        assign_team=str(uuid.uuid4()),
        add_tags=["sales", "vip"],
        priority="high",
    )
    data = step.model_dump(mode="json")
    assert data["type"] == "terminal"
    assert data["add_tags"] == ["sales", "vip"]
    assert data["priority"] == "high"


# --------------------------------------------------------------------------
# Widget config create/update with dialog flow (DB tests)
# --------------------------------------------------------------------------


def test_widget_config_create_with_dialog_flow(db_session: Session):
    """Create a widget config with dialog flow enabled."""
    payload = ChatWidgetConfigCreate(
        name="Test Widget Dialog",
        dialog_flow_enabled=True,
        dialog_flow_steps=[
            DialogFlowStep(
                id="welcome",
                type="choice",
                message="How can we help?",
                options=[DialogFlowOption(label="Sales", next_step="sales_done")],
            ),
            DialogFlowStep(id="sales_done", type="terminal", message="Connecting..."),
        ],
    )
    config = widget_configs.create(db_session, payload)
    assert config.dialog_flow_enabled is True
    assert config.dialog_flow_steps is not None
    assert len(config.dialog_flow_steps) == 2
    assert config.dialog_flow_steps[0]["id"] == "welcome"
    assert config.dialog_flow_steps[1]["type"] == "terminal"


def test_widget_config_create_invalid_dialog_flow(db_session: Session):
    """Creating with enabled but invalid dialog flow should fail."""
    payload = ChatWidgetConfigCreate(
        name="Test Widget Bad Dialog",
        dialog_flow_enabled=True,
        dialog_flow_steps=[],
    )
    with pytest.raises(ValueError, match="requires at least one step"):
        widget_configs.create(db_session, payload)


def test_widget_validate_origin_accepts_full_url_allowed_domain(db_session: Session):
    payload = ChatWidgetConfigCreate(
        name="Origin Match Widget",
        allowed_domains=["https://dotmac.ng", "https://www.dotmac.ng"],
    )
    config = widget_configs.create(db_session, payload)

    assert widget_configs.validate_origin(config, "https://dotmac.ng") is True
    assert widget_configs.validate_origin(config, "https://www.dotmac.ng") is True


# --------------------------------------------------------------------------
# apply_dialog_routing tests
# --------------------------------------------------------------------------


def _make_conversation(db_session: Session, subject: str = "Test"):
    """Create a conversation with required person for DB tests."""
    from app.models.crm.conversation import Conversation
    from app.models.person import Person

    person = Person(
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
    )
    db_session.add(person)
    db_session.flush()

    conversation = Conversation(subject=subject, person_id=person.id)
    db_session.add(conversation)
    db_session.flush()
    return conversation


def test_apply_dialog_routing_sets_priority(db_session: Session):
    """apply_dialog_routing should set conversation priority."""
    from app.models.crm.enums import ConversationPriority

    conversation = _make_conversation(db_session, "Test routing")

    step_config = {"type": "terminal", "priority": "high", "add_tags": None, "assign_team": None}
    apply_dialog_routing(db_session, conversation, step_config)

    db_session.refresh(conversation)
    assert conversation.priority == ConversationPriority.high


def test_apply_dialog_routing_adds_tags(db_session: Session):
    """apply_dialog_routing should create conversation tags."""
    from app.models.crm.conversation import ConversationTag

    conversation = _make_conversation(db_session, "Test tags")

    step_config = {"type": "terminal", "priority": None, "add_tags": ["sales", "urgent"], "assign_team": None}
    apply_dialog_routing(db_session, conversation, step_config)

    tags = db_session.query(ConversationTag).filter(ConversationTag.conversation_id == conversation.id).all()
    tag_values = {t.tag for t in tags}
    assert tag_values == {"sales", "urgent"}


def test_apply_dialog_routing_no_duplicate_tags(db_session: Session):
    """apply_dialog_routing should not duplicate existing tags."""
    from app.models.crm.conversation import ConversationTag

    conversation = _make_conversation(db_session, "Test dup tags")

    # Pre-add one tag
    db_session.add(ConversationTag(conversation_id=conversation.id, tag="sales"))
    db_session.flush()

    step_config = {"type": "terminal", "priority": None, "add_tags": ["sales", "new"], "assign_team": None}
    apply_dialog_routing(db_session, conversation, step_config)

    tags = db_session.query(ConversationTag).filter(ConversationTag.conversation_id == conversation.id).all()
    tag_values = [t.tag for t in tags]
    assert sorted(tag_values) == ["new", "sales"]
