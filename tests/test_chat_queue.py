"""Tests for capacity-aware assignment, queueing, and promotion.

See app/services/crm/inbox/routing.py (capacity helpers, assign_or_enqueue,
mark_conversation_queued) and app/services/crm/inbox/queue.py (promotion +
queue status).
"""

from datetime import UTC, datetime, timedelta

from app.models.crm.conversation import ConversationAssignment
from app.models.crm.enums import AgentPresenceStatus, ConversationStatus
from app.models.crm.presence import AgentPresence
from app.models.crm.team import CrmAgent, CrmAgentTeam
from app.models.person import Person
from app.schemas.crm.conversation import ConversationCreate
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox import queue as queue_service
from app.services.crm.inbox import routing


def _online(db_session, agent):
    db_session.add(
        AgentPresence(
            agent_id=agent.id,
            status=AgentPresenceStatus.online,
            manual_override_status=None,
            last_seen_at=datetime.now(UTC),
        )
    )
    db_session.commit()


def _conversation(db_session, crm_contact):
    return conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )


def test_available_agents_excludes_at_capacity(db_session, crm_contact, crm_agent, crm_team, crm_agent_team):
    crm_agent.max_concurrent_chats = 1
    db_session.commit()
    _online(db_session, crm_agent)

    # Fill the agent's single slot with an open assigned conversation.
    conv1 = _conversation(db_session, crm_contact)
    conversation_service.assign_conversation(
        db_session, conversation_id=str(conv1.id), agent_id=str(crm_agent.id), team_id=str(crm_team.id)
    )

    assert routing._list_available_agents(db_session, str(crm_team.id)) == []
    assert routing._pick_least_loaded_agent(db_session, str(crm_team.id)) is None


def test_assign_or_enqueue_assigns_when_available(db_session, crm_contact, crm_agent, crm_team, crm_agent_team):
    _online(db_session, crm_agent)
    conv = _conversation(db_session, crm_contact)

    assignment = routing.assign_or_enqueue(db_session, conversation=conv, team_id=str(crm_team.id))

    db_session.refresh(conv)
    assert assignment is not None
    assert assignment.agent_id == crm_agent.id
    assert conv.first_assigned_at is not None
    assert conv.queued_at is None


def test_agent_assignment_closes_current_queue_cycle(db_session, crm_contact, crm_agent, crm_team, crm_agent_team):
    _online(db_session, crm_agent)
    conv = _conversation(db_session, crm_contact)
    conv.queued_at = datetime.now(UTC) - timedelta(minutes=2)
    db_session.commit()

    assignment = conversation_service.assign_conversation(
        db_session,
        conversation_id=str(conv.id),
        agent_id=str(crm_agent.id),
        team_id=str(crm_team.id),
    )

    db_session.refresh(conv)
    assert assignment is not None
    assert assignment.agent_id == crm_agent.id
    assert conv.queued_at is None
    assert conv.last_queued_at is not None
    assert conv.last_queue_assigned_at is not None
    assert conv.last_queue_wait_seconds is not None
    assert conv.last_queue_wait_seconds >= 120


def test_assign_or_enqueue_queues_when_no_one_available(db_session, crm_contact, crm_agent, crm_team, crm_agent_team):
    # Agent is offline -> not available -> conversation is queued.
    db_session.add(
        AgentPresence(
            agent_id=crm_agent.id,
            status=AgentPresenceStatus.offline,
            manual_override_status=AgentPresenceStatus.offline,
            last_seen_at=datetime.now(UTC),
        )
    )
    db_session.commit()
    conv = _conversation(db_session, crm_contact)

    assignment = routing.assign_or_enqueue(db_session, conversation=conv, team_id=str(crm_team.id))

    db_session.refresh(conv)
    assert assignment is not None
    assert assignment.agent_id is None
    assert assignment.team_id == crm_team.id
    assert conv.queued_at is not None
    assert conv.first_assigned_at is None


def test_queued_at_is_idempotent(db_session, crm_contact, crm_team):
    conv = _conversation(db_session, crm_contact)
    routing.mark_conversation_queued(db_session, conv)
    first = conv.queued_at
    assert first is not None
    routing.mark_conversation_queued(db_session, conv)
    assert conv.queued_at == first


def test_promote_next_for_agent_pulls_oldest_queued(db_session, crm_contact, crm_agent, crm_team, crm_agent_team):
    _online(db_session, crm_agent)
    # A queued (team-only) conversation in the agent's team.
    conv = _conversation(db_session, crm_contact)
    conversation_service.assign_conversation(
        db_session, conversation_id=str(conv.id), agent_id=None, team_id=str(crm_team.id)
    )
    routing.mark_conversation_queued(db_session, conv)

    promoted = queue_service.promote_next_for_agent(db_session, crm_agent.id)

    assert promoted == 1
    # The conversation now has an active agent assignment.
    from app.models.crm.conversation import ConversationAssignment

    active = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conv.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    assert active.agent_id == crm_agent.id


def test_promote_respects_capacity(db_session, crm_contact, crm_agent, crm_team, crm_agent_team):
    crm_agent.max_concurrent_chats = 1
    db_session.commit()
    _online(db_session, crm_agent)
    # Fill the single slot.
    busy = _conversation(db_session, crm_contact)
    conversation_service.assign_conversation(
        db_session, conversation_id=str(busy.id), agent_id=str(crm_agent.id), team_id=str(crm_team.id)
    )
    # A second conversation waits in the queue.
    waiting = _conversation(db_session, crm_contact)
    conversation_service.assign_conversation(
        db_session, conversation_id=str(waiting.id), agent_id=None, team_id=str(crm_team.id)
    )
    routing.mark_conversation_queued(db_session, waiting)

    promoted = queue_service.promote_next_for_agent(db_session, crm_agent.id)
    assert promoted == 0  # agent is full


def test_promote_queue_skips_ai_owned(db_session, crm_contact, crm_agent, crm_team, crm_agent_team):
    _online(db_session, crm_agent)
    conv = _conversation(db_session, crm_contact)
    conv.metadata_ = {"ai_intake": {"status": "pending"}}
    db_session.add(
        ConversationAssignment(
            conversation_id=conv.id,
            agent_id=None,
            team_id=crm_team.id,
            is_active=True,
        )
    )
    routing.mark_conversation_queued(db_session, conv)
    db_session.commit()

    result = queue_service.promote_queued_conversations(db_session)
    assert result["promoted"] == 0  # AI-owned row is skipped


def test_queue_status_reports_position_and_none_when_assigned(
    db_session, crm_contact, crm_agent, crm_team, crm_agent_team
):
    # Two queued conversations; the older one is position 1.
    older = _conversation(db_session, crm_contact)
    conversation_service.assign_conversation(
        db_session, conversation_id=str(older.id), agent_id=None, team_id=str(crm_team.id)
    )
    older.queued_at = datetime.now(UTC) - timedelta(minutes=5)
    db_session.commit()

    newer = _conversation(db_session, crm_contact)
    conversation_service.assign_conversation(
        db_session, conversation_id=str(newer.id), agent_id=None, team_id=str(crm_team.id)
    )
    newer.queued_at = datetime.now(UTC)
    db_session.commit()

    status_older = queue_service.queue_status_for_conversation(db_session, older)
    status_newer = queue_service.queue_status_for_conversation(db_session, newer)
    assert status_older["position"] == 1
    assert status_newer["position"] == 2
    assert status_newer["estimated_wait_seconds"] >= 0

    # Once assigned to an agent, there is no queue status.
    _online(db_session, crm_agent)
    conversation_service.assign_conversation(
        db_session, conversation_id=str(older.id), agent_id=str(crm_agent.id), team_id=str(crm_team.id)
    )
    assert queue_service.queue_status_for_conversation(db_session, older) is None


def test_second_agent_under_cap_is_available(db_session, crm_contact, crm_agent, crm_team, crm_agent_team):
    # crm_agent at cap, a second agent free -> only the second is available.
    crm_agent.max_concurrent_chats = 1
    db_session.commit()
    _online(db_session, crm_agent)
    busy = _conversation(db_session, crm_contact)
    conversation_service.assign_conversation(
        db_session, conversation_id=str(busy.id), agent_id=str(crm_agent.id), team_id=str(crm_team.id)
    )

    p2 = Person(first_name="Free", last_name="Agent", email="free-agent@example.com")
    db_session.add(p2)
    db_session.commit()
    db_session.refresh(p2)
    a2 = CrmAgent(person_id=p2.id, title="Backup")
    db_session.add(a2)
    db_session.commit()
    db_session.refresh(a2)
    db_session.add(CrmAgentTeam(agent_id=a2.id, team_id=crm_team.id))
    db_session.commit()
    _online(db_session, a2)

    available_ids = {a.id for a in routing._list_available_agents(db_session, str(crm_team.id))}
    assert available_ids == {a2.id}
    assert routing._pick_least_loaded_agent(db_session, str(crm_team.id)) == str(a2.id)


def test_status_open_pending_count_as_active(db_session, crm_contact, crm_agent, crm_team, crm_agent_team):
    """Resolved chats free a slot; open ones count."""
    _online(db_session, crm_agent)
    conv = _conversation(db_session, crm_contact)
    conversation_service.assign_conversation(
        db_session, conversation_id=str(conv.id), agent_id=str(crm_agent.id), team_id=str(crm_team.id)
    )
    assert routing._agent_active_chat_counts(db_session, [crm_agent.id]).get(crm_agent.id) == 1

    conv.status = ConversationStatus.resolved
    db_session.commit()
    assert routing._agent_active_chat_counts(db_session, [crm_agent.id]).get(crm_agent.id, 0) == 0


def test_queue_wait_metrics_aggregates(db_session, crm_contact, crm_team):
    from app.services.crm import reports as crm_reports

    now = datetime.now(UTC)
    conv = _conversation(db_session, crm_contact)
    conv.queued_at = now - timedelta(minutes=10)
    conv.first_assigned_at = now - timedelta(minutes=8)  # 120s wait
    db_session.commit()

    result = crm_reports.queue_wait_metrics(db_session, now - timedelta(days=1), now + timedelta(minutes=1))
    assert result["overall"]["count"] == 1
    assert result["overall"]["median_seconds"] == 120


def test_queue_wait_metrics_uses_completed_queue_cycle(db_session, crm_contact):
    from app.services.crm import reports as crm_reports

    now = datetime.now(UTC)
    conv = _conversation(db_session, crm_contact)
    conv.first_assigned_at = now - timedelta(days=2)
    conv.last_queued_at = now - timedelta(minutes=5)
    conv.last_queue_assigned_at = now - timedelta(minutes=1)
    conv.last_queue_wait_seconds = 240
    db_session.commit()

    result = crm_reports.queue_wait_metrics(db_session, now - timedelta(hours=1), now + timedelta(minutes=1))

    assert result["overall"]["count"] == 1
    assert result["overall"]["median_seconds"] == 240


def test_issue_classification_breakdown_groups(db_session, crm_contact, crm_team):
    from app.models.crm.conversation import ConversationTag
    from app.services.crm import reports as crm_reports

    now = datetime.now(UTC)
    conv = _conversation(db_session, crm_contact)
    conv.metadata_ = {"ai_intake": {"department": "billing"}}
    conv.resolution_time_seconds = 600
    db_session.add(ConversationTag(conversation_id=conv.id, tag="refund"))
    db_session.commit()

    result = crm_reports.issue_classification_breakdown(db_session, now - timedelta(days=1), now + timedelta(minutes=1))
    depts = {d["department"]: d for d in result["departments"]}
    assert depts["billing"]["count"] == 1
    assert depts["billing"]["median_resolution_seconds"] == 600
    assert {"tag": "refund", "count": 1} in result["tags"]


def test_transfer_records_note_in_audit(db_session, crm_contact, crm_agent, crm_team, crm_agent_team, person):
    from app.services.crm.inbox import audit as audit_module
    from app.services.crm.inbox.conversation_actions import assign_conversation as assign_action

    _online(db_session, crm_agent)
    conv = _conversation(db_session, crm_contact)

    captured = {}
    orig = audit_module.log_conversation_action

    def _spy(db, *, action, conversation_id, actor_id=None, metadata=None):
        if action == "assign_conversation":
            captured.update(metadata or {})
        return orig(db, action=action, conversation_id=conversation_id, actor_id=actor_id, metadata=metadata)

    import app.services.crm.inbox.conversation_actions as ca

    ca.log_conversation_action = _spy
    try:
        result = assign_action(
            db_session,
            conversation_id=str(conv.id),
            agent_id=str(crm_agent.id),
            team_id=str(crm_team.id),
            assigned_by_id=str(person.id),
            note="  Escalating to billing specialist  ",
        )
    finally:
        ca.log_conversation_action = orig

    assert result.kind == "success"
    assert captured.get("note") == "Escalating to billing specialist"


def test_agent_availability_map_marks_full(db_session, crm_contact, crm_agent, crm_team, crm_agent_team):
    from app.services.crm.teams.service import _agent_availability_map

    crm_agent.max_concurrent_chats = 1
    db_session.commit()
    _online(db_session, crm_agent)
    busy = _conversation(db_session, crm_contact)
    conversation_service.assign_conversation(
        db_session, conversation_id=str(busy.id), agent_id=str(crm_agent.id), team_id=str(crm_team.id)
    )

    avail = _agent_availability_map(db_session, [crm_agent])
    entry = avail[str(crm_agent.id)]
    assert entry["active_chats"] == 1
    assert entry["cap"] == 1
    assert entry["full"] is True
    assert entry["status"] == "online"


def test_manual_team_only_assignment_sets_queued_at(db_session, crm_contact, crm_agent, crm_team, crm_agent_team):
    """A manual team-only assignment must enqueue (queued_at) so the promotion
    sweep can later hand it to an agent."""
    from app.services.crm.inbox.conversation_actions import assign_conversation as assign_action

    conv = _conversation(db_session, crm_contact)
    result = assign_action(
        db_session,
        conversation_id=str(conv.id),
        agent_id=None,
        team_id=str(crm_team.id),
        assigned_by_id=None,
    )
    db_session.refresh(conv)
    assert result.kind == "success"
    assert conv.queued_at is not None
    # And the periodic sweep can now promote it once an agent is available.
    _online(db_session, crm_agent)
    swept = queue_service.promote_queued_conversations(db_session)
    assert swept["promoted"] == 1
