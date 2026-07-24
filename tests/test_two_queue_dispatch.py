"""Focused unit coverage for the authoritative CRM two-queue dispatcher."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ChannelType, ConversationQueueState, ConversationQueueType, ConversationStatus
from app.models.crm.queue import ConversationQueueDispatchState
from app.models.crm.team import CrmAgent
from app.services.crm.conversations import service as conversation_service
from app.services.crm.inbox import dispatch


def _conversation(contact_id, *, created_at: datetime) -> Conversation:
    return Conversation(
        person_id=contact_id,
        status=ConversationStatus.open,
        is_active=True,
        created_at=created_at,
    )


def test_department_mapping_has_exactly_two_logical_queues():
    assert dispatch.queue_for_department("support") == ConversationQueueType.support
    assert dispatch.queue_for_department("billing_payment") == ConversationQueueType.sales
    assert dispatch.queue_for_department("billing_renewal") == ConversationQueueType.sales
    assert dispatch.queue_for_department("sales") == ConversationQueueType.sales
    assert dispatch.queue_for_department(None) == ConversationQueueType.support


def test_position_is_fifo_by_original_arrival_then_entry_id(db_session, crm_contact):
    now = datetime.now(UTC)
    first = _conversation(crm_contact.id, created_at=now - timedelta(minutes=2))
    second = _conversation(crm_contact.id, created_at=now - timedelta(minutes=1))
    db_session.add_all([first, second])
    db_session.flush()

    first_entry = dispatch.enqueue_classified(
        db_session, conversation=first, queue_type=ConversationQueueType.support, notify_initial=False
    )
    second_entry = dispatch.enqueue_classified(
        db_session, conversation=second, queue_type=ConversationQueueType.support, notify_initial=False
    )
    db_session.commit()

    assert dispatch.position_for_entry(db_session, first_entry) == 1
    assert dispatch.position_for_entry(db_session, second_entry) == 2


def test_manager_cannot_assign_a_non_head(db_session, crm_contact):
    now = datetime.now(UTC)
    first = _conversation(crm_contact.id, created_at=now - timedelta(minutes=2))
    second = _conversation(crm_contact.id, created_at=now - timedelta(minutes=1))
    db_session.add_all([first, second])
    db_session.flush()
    dispatch.enqueue_classified(
        db_session, conversation=first, queue_type=ConversationQueueType.support, notify_initial=False
    )
    dispatch.enqueue_classified(
        db_session, conversation=second, queue_type=ConversationQueueType.support, notify_initial=False
    )
    db_session.commit()

    with pytest.raises(HTTPException, match="position 2"):
        dispatch.manager_assign_head(
            db_session,
            conversation_id=str(second.id),
            agent_id="00000000-0000-0000-0000-000000000000",
            actor_id="00000000-0000-0000-0000-000000000000",
        )


def test_agent_cap_is_never_higher_than_twenty(crm_agent):
    crm_agent.max_concurrent_chats = 30
    assert dispatch._agent_cap(crm_agent) == 20
    crm_agent.max_concurrent_chats = 10
    assert dispatch._agent_cap(crm_agent) == 10
    assert dispatch._agent_cap(CrmAgent(person_id=crm_agent.person_id)) == 20


def test_round_robin_uses_durable_cursor_and_wraps_past_ineligible_agents(monkeypatch):
    agents = [CrmAgent(id=uuid4(), person_id=uuid4()) for _ in range(3)]
    state = ConversationQueueDispatchState(
        queue_type=ConversationQueueType.support,
        round_robin_cursor_agent_id=agents[0].id,
    )
    monkeypatch.setattr(dispatch, "_queue_agents", lambda *_args: agents)
    monkeypatch.setattr(dispatch.routing, "_agent_active_chat_counts", lambda *_args: {})
    monkeypatch.setattr(dispatch, "_agent_is_eligible", lambda _db, agent, _loads: agent.id == agents[2].id)

    assert dispatch._round_robin_agent(None, ConversationQueueType.support, state) is agents[2]
    # Completing/requeuing an entry never changes the state cursor; the next
    # independent worker therefore starts at the same deterministic point.
    assert state.round_robin_cursor_agent_id == agents[0].id


def test_position_notice_marks_crossed_milestones_once(db_session, crm_contact, monkeypatch):
    conversation = _conversation(crm_contact.id, created_at=datetime.now(UTC))
    db_session.add(conversation)
    db_session.flush()
    entry = dispatch.enqueue_classified(
        db_session,
        conversation=conversation,
        queue_type=ConversationQueueType.support,
        notify_initial=False,
    )
    entry.state = ConversationQueueState.waiting
    entry.position_tracking = {"support": {"last_observed_position": 21, "sent_milestones": []}}
    db_session.commit()
    notices: list[str] = []
    monkeypatch.setattr(dispatch, "position_for_entry", lambda *_args: 9)
    monkeypatch.setattr(
        dispatch,
        "_notice",
        lambda _db, _entry, *, key, body: notices.append(key) is None,
    )

    assert dispatch.emit_position_notices(db_session) == 1
    assert notices == ["position:support:20,10:9"]
    assert entry.position_tracking["support"]["sent_milestones"] == [20, 10]


def test_backfill_dry_run_never_creates_queue_entries(db_session, crm_contact):
    conversation = _conversation(crm_contact.id, created_at=datetime.now(UTC))
    db_session.add(conversation)
    db_session.commit()

    report = dispatch.backfill_unresolved(db_session)

    assert report["mode"] == "dry_run"
    assert report["support"] == 1
    assert dispatch.active_entry(db_session, str(conversation.id)) is None


def test_cutover_readiness_reports_unresolved_conversation_without_cycle(db_session, crm_contact):
    conversation = _conversation(crm_contact.id, created_at=datetime.now(UTC))
    db_session.add(conversation)
    db_session.commit()

    readiness = dispatch.cutover_readiness(db_session)

    assert readiness["ready"] is False
    assert str(conversation.id) in readiness["missing_live_cycles"]
    assert readiness["scheduled_worker_exists"] is False


def test_generic_assignment_cannot_bypass_dispatch_owner(db_session, crm_contact, monkeypatch):
    conversation = _conversation(crm_contact.id, created_at=datetime.now(UTC))
    db_session.add(conversation)
    db_session.commit()
    monkeypatch.setattr(dispatch, "enabled", lambda _db: True)
    monkeypatch.setattr(conversation_service, "get_reply_channel_type", lambda *_args: ChannelType.whatsapp)

    with pytest.raises(HTTPException, match="FIFO queue owner"):
        conversation_service.assign_conversation(
            db_session,
            conversation_id=str(conversation.id),
            agent_id=str(uuid4()),
            assigned_by_id=str(uuid4()),
        )
