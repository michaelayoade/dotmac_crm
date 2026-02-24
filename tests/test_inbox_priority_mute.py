"""Tests for conversation priority, mute, auto-resolve, transcript, and template search."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ConversationPriority, ConversationStatus
from app.models.crm.team import CrmAgent
from app.models.person import Person


def _unique_email() -> str:
    return f"prio-{uuid.uuid4().hex[:8]}@example.com"


def _create_person(db_session, *, name: str = "Test") -> Person:
    person = Person(first_name=name, last_name="Contact", email=_unique_email())
    db_session.add(person)
    db_session.flush()
    return person


def _create_agent(db_session, person: Person) -> CrmAgent:
    agent = CrmAgent(person_id=person.id, title="Agent")
    db_session.add(agent)
    db_session.flush()
    return agent


def _create_conversation(
    db_session,
    contact: Person,
    *,
    priority: ConversationPriority = ConversationPriority.none,
    is_muted: bool = False,
    status: ConversationStatus = ConversationStatus.open,
) -> Conversation:
    conv = Conversation(
        person_id=contact.id,
        status=status,
        priority=priority,
        is_muted=is_muted,
    )
    db_session.add(conv)
    db_session.flush()
    return conv


def _result_ids(results: list[tuple]) -> list[uuid.UUID]:
    return [row[0].id for row in results]


# ── Priority Enum Tests ─────────────────────────────────────


class TestConversationPriorityEnum:
    def test_enum_values(self):
        assert ConversationPriority.none.value == "none"
        assert ConversationPriority.low.value == "low"
        assert ConversationPriority.medium.value == "medium"
        assert ConversationPriority.high.value == "high"
        assert ConversationPriority.urgent.value == "urgent"

    def test_enum_has_five_members(self):
        assert len(ConversationPriority) == 5


# ── Priority Model Tests ────────────────────────────────────


class TestConversationPriority:
    def test_default_priority_is_none(self, db_session):
        contact = _create_person(db_session)
        conv = Conversation(person_id=contact.id, status=ConversationStatus.open)
        db_session.add(conv)
        db_session.flush()
        assert conv.priority == ConversationPriority.none

    def test_set_priority(self, db_session):
        contact = _create_person(db_session)
        conv = _create_conversation(db_session, contact, priority=ConversationPriority.urgent)
        assert conv.priority == ConversationPriority.urgent

    def test_update_priority(self, db_session):
        contact = _create_person(db_session)
        conv = _create_conversation(db_session, contact, priority=ConversationPriority.low)
        conv.priority = ConversationPriority.high
        db_session.flush()
        db_session.refresh(conv)
        assert conv.priority == ConversationPriority.high


# ── Priority Filter Tests ───────────────────────────────────


class TestPriorityFilter:
    def test_filter_by_priority(self, db_session):
        from app.services.crm.inbox.queries import list_inbox_conversations

        contact = _create_person(db_session)
        conv_high = _create_conversation(db_session, contact, priority=ConversationPriority.high)
        conv_low = _create_conversation(db_session, contact, priority=ConversationPriority.low)
        db_session.flush()

        results = list_inbox_conversations(db_session, priority=ConversationPriority.high)
        ids = _result_ids(results)
        assert conv_high.id in ids
        assert conv_low.id not in ids

    def test_sort_by_priority(self, db_session):
        from app.services.crm.inbox.queries import list_inbox_conversations

        contact = _create_person(db_session)
        conv_none = _create_conversation(db_session, contact, priority=ConversationPriority.none)
        conv_urgent = _create_conversation(db_session, contact, priority=ConversationPriority.urgent)
        conv_medium = _create_conversation(db_session, contact, priority=ConversationPriority.medium)
        db_session.flush()

        results = list_inbox_conversations(db_session, sort_by="priority")
        ids = _result_ids(results)
        # Urgent should come first, then medium, then none
        urgent_idx = ids.index(conv_urgent.id)
        medium_idx = ids.index(conv_medium.id)
        none_idx = ids.index(conv_none.id)
        assert urgent_idx < medium_idx < none_idx


# ── Mute Model Tests ────────────────────────────────────────


class TestConversationMute:
    def test_default_is_not_muted(self, db_session):
        contact = _create_person(db_session)
        conv = Conversation(person_id=contact.id, status=ConversationStatus.open)
        db_session.add(conv)
        db_session.flush()
        assert conv.is_muted is False

    def test_toggle_mute(self, db_session):
        contact = _create_person(db_session)
        conv = _create_conversation(db_session, contact)
        assert conv.is_muted is False

        conv.is_muted = True
        db_session.flush()
        db_session.refresh(conv)
        assert conv.is_muted is True

        conv.is_muted = False
        db_session.flush()
        db_session.refresh(conv)
        assert conv.is_muted is False


# ── Mute Notification Suppression Tests ──────────────────────


class TestMuteNotifications:
    def test_muted_conversation_skips_notification(self, db_session):
        """notify_assigned_agent_new_reply should skip muted conversations even with an assigned agent."""
        from unittest.mock import patch

        from app.models.crm.conversation import ConversationAssignment
        from app.models.crm.enums import ChannelType as CrmChannelType
        from app.models.crm.enums import MessageDirection
        from app.services.crm.inbox.notifications import notify_assigned_agent_new_reply

        contact = _create_person(db_session)
        agent_person = _create_person(db_session, name="MuteAgent")
        agent = _create_agent(db_session, agent_person)

        conv = _create_conversation(db_session, contact, is_muted=True)
        # Assign agent to the conversation
        assignment = ConversationAssignment(
            conversation_id=conv.id,
            agent_id=agent.id,
            is_active=True,
        )
        db_session.add(assignment)

        msg = Message(
            conversation_id=conv.id,
            author_id=contact.id,
            body="Test inbound message",
            channel_type=CrmChannelType.email,
            direction=MessageDirection.inbound,
        )
        db_session.add(msg)
        db_session.flush()

        # Mute check should cause early return before broadcast is called
        with patch("app.websocket.broadcaster.broadcast_agent_notification") as mock_broadcast:
            notify_assigned_agent_new_reply(db_session, conv, msg)
            mock_broadcast.assert_not_called()


# ── Formatting Tests ─────────────────────────────────────────


class TestFormatting:
    def test_format_includes_priority_and_muted(self, db_session):
        from app.services.crm.inbox.formatting import format_conversation_for_template

        contact = _create_person(db_session)
        conv = _create_conversation(db_session, contact, priority=ConversationPriority.high, is_muted=True)
        db_session.flush()

        result = format_conversation_for_template(conv, db_session)
        assert result["priority"] == "high"
        assert result["is_muted"] is True

    def test_format_default_priority(self, db_session):
        from app.services.crm.inbox.formatting import format_conversation_for_template

        contact = _create_person(db_session)
        conv = _create_conversation(db_session, contact)
        db_session.flush()

        result = format_conversation_for_template(conv, db_session)
        assert result["priority"] == "none"
        assert result["is_muted"] is False


# ── Auto-Resolve Tests ───────────────────────────────────────


class TestAutoResolve:
    @patch("app.services.crm.inbox.auto_resolve.resolve_value")
    def test_auto_resolve_disabled_returns_early(self, mock_resolve, db_session):
        from app.services.crm.inbox.auto_resolve import auto_resolve_idle_conversations

        mock_resolve.side_effect = lambda db, domain, key: {
            "crm_inbox_auto_resolve_enabled": False,
            "crm_inbox_auto_resolve_days": 7,
        }.get(key)

        result = auto_resolve_idle_conversations(db_session)
        assert result.get("skipped") is True

    @patch("app.services.crm.inbox.auto_resolve.resolve_value")
    @patch("app.services.crm.inbox.auto_resolve.inbox_cache")
    def test_auto_resolve_resolves_idle_conversations(self, mock_cache, mock_resolve, db_session):
        from app.services.crm.inbox.auto_resolve import auto_resolve_idle_conversations

        mock_resolve.side_effect = lambda db, domain, key: {
            "crm_inbox_auto_resolve_enabled": True,
            "crm_inbox_auto_resolve_days": 3,
        }.get(key)

        contact = _create_person(db_session)
        # Idle conversation (last activity 5 days ago)
        conv_idle = _create_conversation(db_session, contact, status=ConversationStatus.open)
        conv_idle.last_message_at = datetime.now(UTC) - timedelta(days=5)
        # Fresh conversation (last activity today)
        conv_fresh = _create_conversation(db_session, contact, status=ConversationStatus.open)
        conv_fresh.last_message_at = datetime.now(UTC)
        db_session.flush()

        result = auto_resolve_idle_conversations(db_session)
        assert result["resolved"] >= 1

        db_session.refresh(conv_idle)
        db_session.refresh(conv_fresh)
        assert conv_idle.status == ConversationStatus.resolved
        assert conv_fresh.status == ConversationStatus.open

    @patch("app.services.crm.inbox.auto_resolve.resolve_value")
    def test_auto_resolve_skips_fresh_conversations(self, mock_resolve, db_session):
        from app.services.crm.inbox.auto_resolve import auto_resolve_idle_conversations

        mock_resolve.side_effect = lambda db, domain, key: {
            "crm_inbox_auto_resolve_enabled": True,
            "crm_inbox_auto_resolve_days": 7,
        }.get(key)

        contact = _create_person(db_session)
        conv = _create_conversation(db_session, contact, status=ConversationStatus.open)
        conv.last_message_at = datetime.now(UTC) - timedelta(days=2)
        db_session.flush()

        result = auto_resolve_idle_conversations(db_session)
        assert result["resolved"] == 0

        db_session.refresh(conv)
        assert conv.status == ConversationStatus.open


# ── Template Search Tests ────────────────────────────────────


class TestTemplateSearch:
    def test_search_returns_matching_templates(self, db_session):
        from app.models.crm.enums import ChannelType as CrmChannelType
        from app.models.crm.message_template import CrmMessageTemplate
        from app.services.crm.inbox.templates import message_templates

        tpl = CrmMessageTemplate(
            name="thanks_response",
            body="Thank you for contacting us!",
            channel_type=CrmChannelType.email,
            is_active=True,
        )
        db_session.add(tpl)
        db_session.flush()

        results = message_templates.search(db_session, "thanks")
        assert len(results) >= 1
        assert any(t.name == "thanks_response" for t in results)

    def test_search_case_insensitive(self, db_session):
        from app.models.crm.enums import ChannelType as CrmChannelType
        from app.models.crm.message_template import CrmMessageTemplate
        from app.services.crm.inbox.templates import message_templates

        tpl = CrmMessageTemplate(
            name="Greeting",
            body="Hello! How can I help?",
            channel_type=CrmChannelType.email,
            is_active=True,
        )
        db_session.add(tpl)
        db_session.flush()

        results = message_templates.search(db_session, "greeting")
        assert len(results) >= 1

    def test_search_excludes_inactive(self, db_session):
        from app.models.crm.enums import ChannelType as CrmChannelType
        from app.models.crm.message_template import CrmMessageTemplate
        from app.services.crm.inbox.templates import message_templates

        tpl = CrmMessageTemplate(
            name="archived_template",
            body="Old content",
            channel_type=CrmChannelType.email,
            is_active=False,
        )
        db_session.add(tpl)
        db_session.flush()

        results = message_templates.search(db_session, "archived_template")
        assert len(results) == 0

    def test_search_empty_query_returns_empty(self, db_session):
        from app.services.crm.inbox.templates import message_templates

        results = message_templates.search(db_session, "")
        assert len(results) == 0


# ── Transcript Service Tests ─────────────────────────────────


class TestTranscript:
    @patch("app.services.email.send_email")
    def test_send_transcript_calls_email(self, mock_send_email, db_session):
        from app.models.crm.enums import ChannelType as CrmChannelType
        from app.models.crm.enums import MessageDirection
        from app.services.crm.inbox.transcript import send_conversation_transcript

        contact = _create_person(db_session)
        conv = _create_conversation(db_session, contact)
        # Add a message
        msg = Message(
            conversation_id=conv.id,
            author_id=contact.id,
            body="Test message content",
            channel_type=CrmChannelType.email,
            direction=MessageDirection.inbound,
        )
        db_session.add(msg)
        db_session.flush()

        mock_send_email.return_value = True

        agent_person = _create_person(db_session, name="Agent")
        success, error = send_conversation_transcript(
            db_session, str(conv.id), "recipient@example.com", str(agent_person.id)
        )
        assert success is True
        assert error is None
        mock_send_email.assert_called_once()

    def test_send_transcript_invalid_conversation(self, db_session):
        from app.services.crm.inbox.transcript import send_conversation_transcript

        success, error = send_conversation_transcript(
            db_session, str(uuid.uuid4()), "recipient@example.com", str(uuid.uuid4())
        )
        assert success is False
        assert error is not None
