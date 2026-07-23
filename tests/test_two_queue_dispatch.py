"""Focused unit coverage for the authoritative CRM two-queue dispatcher."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ConversationQueueType, ConversationStatus
from app.models.crm.team import CrmAgent
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
